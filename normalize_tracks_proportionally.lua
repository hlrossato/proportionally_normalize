-- =============================================================================
-- Proportional Clip Gain Normalizer — All Tracks with Content
-- =============================================================================

local function dBToLinear(dB) return 10 ^ (dB / 20) end
local function linearToDB(linear)
  if linear <= 0 then return -math.huge end
  return 20 * math.log(linear, 10)
end

-- ── DIALOG ───────────────────────────────────────────────────────────────────
local function showDialog()
  local ok, result = reaper.GetUserInputs(
    "Proportional Clip Gain Normalizer", 2,
    "Target peak (dBFS),Target average / RMS (dBFS)",
    "-12,-18"
  )
  if not ok then return nil, nil end

  local peakVal = tonumber(result:match("^([^,]+),"))
  local avgVal  = tonumber(result:match(",(.+)$"))

  if not peakVal or not avgVal then
    reaper.ShowMessageBox("Invalid input. Please enter numeric dBFS values.", "Error", 0)
    return nil, nil
  end
  if peakVal > 0 or avgVal > 0 then
    reaper.ShowMessageBox("Values must be negative dBFS numbers.", "Error", 0)
    return nil, nil
  end
  if avgVal >= peakVal then
    reaper.ShowMessageBox("Average must be lower than peak (e.g. peak -12, average -18).", "Error", 0)
    return nil, nil
  end

  return peakVal, avgVal
end

-- ── PEAK SCAN ────────────────────────────────────────────────────────────────
local BLOCK_SAMPLES = 65536

local function getTakePeak(take)
  if not take or reaper.TakeIsMIDI(take) then return 0 end

  local src = reaper.GetMediaItemTake_Source(take)
  if not src then return 0 end

  local numCh    = reaper.GetMediaSourceNumChannels(src)
  local srate    = reaper.GetMediaSourceSampleRate(src)
  local duration = reaper.GetMediaSourceLength(src)
  if numCh == 0 or srate == 0 or duration <= 0 then return 0 end

  local accessor = reaper.CreateTakeAudioAccessor(take)
  if not accessor then return 0 end

  local bufSize   = BLOCK_SAMPLES * numCh
  local buf       = reaper.new_array(bufSize)
  local maxPeak   = 0
  local blockSecs = BLOCK_SAMPLES / srate
  local t         = 0.0

  while t < duration do
    if reaper.GetAudioAccessorSamples(accessor, srate, numCh, t, BLOCK_SAMPLES, buf) == 1 then
      for j = 1, bufSize do
        local v = buf[j]
        if v then
          if  v > maxPeak then maxPeak =  v end
          if -v > maxPeak then maxPeak = -v end
        end
      end
    end
    t = t + blockSecs
  end

  reaper.DestroyAudioAccessor(accessor)
  return maxPeak
end

-- ── MAIN ─────────────────────────────────────────────────────────────────────
local function main()
  local numTracks = reaper.CountTracks(0)
  if numTracks == 0 then
    reaper.ShowMessageBox("No tracks in project.", "Proportional Normalizer", 0)
    return
  end

  local targetPeakDB, targetAvgDB = showDialog()
  if not targetPeakDB then return end

  local thresholdLinear = dBToLinear(targetPeakDB)

  -- Pass 1: scan everything, build work list — no modifications yet
  local workList = {}

  for i = 0, numTracks - 1 do
    local track    = reaper.GetTrack(0, i)
    local numItems = reaper.CountTrackMediaItems(track)
    if numItems == 0 then goto skipTrack end

    for ii = 0, numItems - 1 do
      local item   = reaper.GetTrackMediaItem(track, ii)
      local nTakes = reaper.CountTakes(item)
      if nTakes == 0 then goto continue end

      local sourcePeak = 0
      for t = 0, nTakes - 1 do
        local p = getTakePeak(reaper.GetTake(item, t))
        if p > sourcePeak then sourcePeak = p end
      end

      if sourcePeak > 0 then
        local currentVol    = reaper.GetMediaItemInfo_Value(item, "D_VOL")
        local effectivePeak = sourcePeak * currentVol
        if effectivePeak > thresholdLinear then
          local gainDB = targetPeakDB - linearToDB(effectivePeak)
          workList[#workList + 1] = { item = item, newVol = currentVol * dBToLinear(gainDB) }
        end
      end

      ::continue::
    end
    ::skipTrack::
  end

  if #workList == 0 then return end

  -- Pass 2: apply all gain changes
  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)

  for _, entry in ipairs(workList) do
    reaper.SetMediaItemInfo_Value(entry.item, "D_VOL", entry.newVol)
  end

  -- Pass 3: update all waveforms
  for _, entry in ipairs(workList) do
    reaper.UpdateItemInProject(entry.item)
  end

  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock(
    string.format("Proportional Clip Gain Normalize — peak %d dBFS", targetPeakDB), -1)
end

main()
