# =============================================================================
# Proportional Clip Gain Normalizer — Python 3 ReaScript
#
# Install: Actions → New action → Load ReaScript → select this .py file
# Requires Reaper 6+ with Python 3 enabled in Preferences → ReaScript
# =============================================================================

from reaper_python import *
import struct
import math

# ── HELPERS ──────────────────────────────────────────────────────────────────
def db_to_linear(dB):
    return 10 ** (dB / 20)

def linear_to_db(linear):
    if linear <= 0:
        return -math.inf
    return 20 * math.log10(linear)

# ── DIALOG ───────────────────────────────────────────────────────────────────
def show_dialog():
    # RPR_GetUserInputs returns (ok, title, num_fields, captions, retvals)
    ok, _, _, _, result = RPR_GetUserInputs(
        "Proportional Clip Gain Normalizer", 2,
        "Target peak (dBFS),Target average / RMS (dBFS)",
        "-12,-18", 64
    )
    if not ok:
        return None, None

    parts = result.split(",")
    if len(parts) != 2:
        RPR_ShowMessageBox(
            "Invalid input. Please enter numeric dBFS values (e.g. -12 and -18).",
            "Proportional Normalizer — Error", 0
        )
        return None, None

    try:
        peak_val = float(parts[0].strip())
        avg_val  = float(parts[1].strip())
    except ValueError:
        RPR_ShowMessageBox(
            "Invalid input. Please enter numeric dBFS values (e.g. -12 and -18).",
            "Proportional Normalizer — Error", 0
        )
        return None, None

    if peak_val > 0 or avg_val > 0:
        RPR_ShowMessageBox(
            "Values should be negative dBFS numbers (e.g. -12, -18).\nPositive values would clip — aborting.",
            "Proportional Normalizer — Error", 0
        )
        return None, None

    if avg_val >= peak_val:
        RPR_ShowMessageBox(
            f"Target average ({avg_val} dBFS) should be lower than target peak ({peak_val} dBFS).\n"
            "Typical values: peak -12, average -18.",
            "Proportional Normalizer — Error", 0
        )
        return None, None

    return peak_val, avg_val

# ── AUDIO ACCESSOR PEAK SCAN ─────────────────────────────────────────────────
BLOCK_SAMPLES = 4096

def get_take_peak(take):
    if not take:
        return 0
    if RPR_TakeIsMIDI(take):
        return 0

    src = RPR_GetMediaItemTake_Source(take)
    if not src:
        return 0

    num_ch   = RPR_GetMediaSourceNumChannels(src)
    srate    = RPR_GetMediaSourceSampleRate(src)
    # GetMediaSourceLength returns (length, src, lengthIsQN)
    duration = RPR_GetMediaSourceLength(src, False)[0]

    if num_ch == 0 or srate == 0 or duration <= 0:
        return 0

    accessor = RPR_CreateTakeAudioAccessor(take)
    if not accessor:
        return 0

    buf_count = BLOCK_SAMPLES * num_ch           # total number of doubles
    buf_bytes = b'\x00' * (buf_count * 8)        # 8 bytes per IEEE-754 double
    max_peak  = 0.0
    t         = 0.0
    block_sec = BLOCK_SAMPLES / srate
    fmt       = f'{buf_count}d'

    while t < duration:
        # RPR_GetAudioAccessorSamples returns (retval, accessor, srate, numch, starttime, numsamp, buf)
        retval, _, _, _, _, _, out_buf = RPR_GetAudioAccessorSamples(
            accessor, srate, num_ch, t, BLOCK_SAMPLES, buf_bytes, buf_count
        )
        if retval == 1:
            samples = struct.unpack_from(fmt, out_buf, 0)
            for s in samples:
                a = abs(s)
                if a > max_peak:
                    max_peak = a
        t += block_sec

    RPR_DestroyAudioAccessor(accessor)
    return max_peak

# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    num_tracks = RPR_CountTracks(0)
    if num_tracks == 0:
        RPR_ShowMessageBox("No tracks found in the project.", "Proportional Normalizer", 0)
        return

    target_peak_db, target_avg_db = show_dialog()
    if target_peak_db is None:
        return

    threshold_linear = db_to_linear(target_peak_db)

    adjusted = []   # tracks where at least one clip was changed
    skipped  = []   # tracks where all clips were already within range
    no_audio = []   # tracks with no readable audio

    RPR_Undo_BeginBlock()

    for i in range(num_tracks):
        track = RPR_GetTrack(0, i)
        # RPR_GetSetMediaTrackInfo_String returns (bool, track, parmname, name, sizeout)
        _, _, _, name, _ = RPR_GetSetMediaTrackInfo_String(track, "P_NAME", "", False)
        if not name:
            name = f"Track {i + 1}"

        num_items       = RPR_CountTrackMediaItems(track)
        track_has_audio = False
        track_adjusted  = False
        track_clips     = []

        for ii in range(num_items):
            item    = RPR_GetTrackMediaItem(track, ii)
            n_takes = RPR_CountTakes(item)

            item_peak = 0.0
            for t in range(n_takes):
                p = get_take_peak(RPR_GetTake(item, t))
                if p > item_peak:
                    item_peak = p

            if item_peak > 0:
                track_has_audio = True

                if item_peak <= threshold_linear:
                    track_clips.append(
                        f"      clip {ii+1:<3}  peak = {linear_to_db(item_peak):+.1f} dBFS  →  no change"
                    )
                else:
                    current_peak_db  = linear_to_db(item_peak)
                    gain_db          = target_peak_db - current_peak_db
                    gain_linear      = db_to_linear(gain_db)
                    current_clip_vol = RPR_GetMediaItemInfo_Value(item, "D_VOL")
                    RPR_SetMediaItemInfo_Value(item, "D_VOL", current_clip_vol * gain_linear)

                    track_adjusted = True
                    track_clips.append(
                        f"      clip {ii+1:<3}  peak = {current_peak_db:+.1f} dBFS"
                        f"  →  applied {gain_db:+.1f} dB"
                        f"  →  new peak = {target_peak_db:+.1f} dBFS"
                    )

        if not track_has_audio:
            no_audio.append(f"  {name:<30}  (no audio items / unreadable source)")
        elif track_adjusted:
            adjusted.append(f"  ▼ {name}")
            adjusted.extend(track_clips)
            adjusted.append("")
        else:
            skipped.append(f"  {name:<30}  all clips within range")

    RPR_Undo_EndBlock(
        f"Proportional Clip Gain Normalize — peak {int(target_peak_db)} dBFS", -1
    )

    # ── Console report ───────────────────────────────────────────────────────
    sep  = "─" * 74
    sep2 = "═" * 74
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║   Proportional Clip Gain Normalizer                                  ║",
        f"║   Target peak: {target_peak_db} dBFS   |   Target average: {target_avg_db} dBFS              ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
    ]

    if adjusted:
        lines.append("▼  ADJUSTED TRACKS  (clip gains applied)")
        lines.append(sep)
        lines.extend(adjusted)

    if skipped:
        s = "" if len(skipped) == 1 else "s"
        lines.append(f"–  SKIPPED  ({len(skipped)} track{s} — all clips already within range)")
        lines.append(sep)
        lines.extend(skipped)
        lines.append("")

    if no_audio:
        s = "" if len(no_audio) == 1 else "s"
        lines.append(f"⚠  NO AUDIO  ({len(no_audio)} track{s})")
        lines.append(sep)
        lines.extend(no_audio)
        lines.append("")

    adjusted_track_count = sum(1 for v in adjusted if v.startswith("  ▼"))

    lines += [
        sep2,
        f"  Result:  {adjusted_track_count} track(s) had clips adjusted"
        f"  |  {len(skipped)} skipped  |  {len(no_audio)} no audio",
        "  Clip gains applied — track faders unchanged.",
        "",
    ]

    RPR_ShowConsoleMsg("\n".join(lines))

    RPR_ShowMessageBox(
        f"Done!\n\n"
        f"Target peak:    {target_peak_db} dBFS\n"
        f"Target average: {target_avg_db} dBFS\n\n"
        f"{adjusted_track_count} track(s) had clips proportionally reduced.\n"
        f"{len(skipped)} track(s) were already within range.\n"
        f"{len(no_audio)} track(s) had no readable audio.\n\n"
        f"Track faders were NOT touched — only clip gains adjusted.\n"
        f"See the Reaper Console for the full per-clip report.",
        "Proportional Normalizer — Done", 0
    )

main()
