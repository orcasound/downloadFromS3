import math
import os
import shutil

# import sys
import time
from datetime import datetime  # , timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import ffmpeg
import m3u8

# from botocore import UNSIGNED
# from botocore.config import Config
from pytz import timezone

from orca_hls_utils import datetime_utils, s3_utils, scraper


def _folders_covering_range(all_folders, start_time, end_time):
    """Return sorted folder timestamps whose live span overlaps [start_time, end_time].

    Each S3 folder name is the unix time the segment started; it holds data
    until the next folder's start time, and the newest folder is still live
    (extends to +infinity). This correctly handles a request that falls
    inside the latest, still-growing folder — the case where the upstream
    s3_utils.get_folders_between_timestamp walks off the end of the list.
    """
    folders = sorted(int(f) for f in all_folders)
    if not folders:
        return []
    valid = []
    for i, f in enumerate(folders):
        folder_end = folders[i + 1] if i + 1 < len(folders) else float("inf")
        if f <= int(end_time) and folder_end > int(start_time):
            valid.append(f)
    return valid


def get_readable_clipname(hydrophone_id, cliptime_utc):
    # cliptime is of the form 2020-09-27T00/16/55.677242Z
    cliptime_utc = timezone("UTC").localize(cliptime_utc)
    date = cliptime_utc.astimezone(timezone("US/Pacific"))
    date_format = "%Y_%m_%d_%H_%M_%S_%Z"
    clipname = date.strftime(date_format)
    return hydrophone_id + "_" + clipname, date


# TODO: Handle date ranges that don't exist
class DateRangeHLSStream:
    """
    stream_base = 'https://s3-us-west-2.amazonaws.com/streaming-orcasound-net/rpi_orcasound_lab' # noqa
    polling_interval = 60 sec
    start_unix_time
    end_unix_time
    wav_dir
    overwrite_output: allows ffmpeg to overwrite output, default is False
    real_time: if False, get data as soon as possible, if true wait for
                polling interval before pulling
    """

    def __init__(
        self,
        stream_base,
        polling_interval,
        start_unix_time,
        end_unix_time,
        wav_dir,
        overwrite_output=False,
        real_time=False,
    ):
        """ """

        # Get all necessary data and create index
        self.stream_base = stream_base
        self.polling_interval_in_seconds = polling_interval
        self.start_unix_time = start_unix_time
        self.end_unix_time = end_unix_time
        self.wav_dir = wav_dir
        self.overwrite_output = overwrite_output
        self.real_time = real_time
        self.is_end_of_stream = False

        # Create wav dir if necessary
        Path(self.wav_dir).mkdir(parents=True, exist_ok=True)

        # query the stream base for all m3u8 files between the timestamps

        # split the stream base into bucket and folder
        # eg.
        # 'https://s3-us-west-2.amazonaws.com/streaming-orcasound-net/rpi_orcasound_lab' # noqa
        # would be split into s3_bucket = 'streaming-orcasound-net' and
        # folder_name = 'rpi_orcasound_lab'

        tokens = self.stream_base.split("/")    # Note, counting on the last two tokens to be s3_bucket and folder_name
        self.s3_bucket = tokens[-2]
        self.folder_name = tokens[-1]
        prefix = self.folder_name + "/hls/"

        # returns folder names corresponding to epochs, this grows as more
        # data is added, we should probably maintain a list of
        # hydrophone folders that exist
        all_hydrophone_folders = s3_utils.get_all_folders(
            self.s3_bucket, prefix=prefix
        )
        print(
            "Found {} folders in all for hydrophone".format(
                len(all_hydrophone_folders)
            )
        )

        # Each folder's timestamp is the start of its segment; it holds data
        # until the next folder begins (and the latest folder is still live,
        # so it extends to "now"). s3_utils.get_folders_between_timestamp
        # walks off the end of the list when start_time is past every
        # folder's timestamp, which happens whenever the requested range is
        # inside the live/latest folder.
        self.valid_folders = _folders_covering_range(
            all_hydrophone_folders, self.start_unix_time, self.end_unix_time
        )
        print("Found {} folders in date range".format(len(self.valid_folders)))

        self.current_folder_index = 0
        self.current_clip_start_time = self.start_unix_time

    def get_next_clip(self, current_clip_name=None):
        # Get current folder
        current_folder = int(self.valid_folders[self.current_folder_index])

        # if real_time execution mode is specified
        if self.real_time:
            # sleep till enough time has elapsed

            now = datetime.utcnow()
            time_to_sleep = (current_clip_name - now).total_seconds()

            if time_to_sleep < 0:
                print("Issue with timing")

            if time_to_sleep > 0:
                time.sleep(time_to_sleep)

        # read in current m3u8 file
        # stream_url for the current AWS folder
        stream_url = "{}/hls/{}/live.m3u8".format(
            (self.stream_base), (current_folder)
        )
        stream_obj = m3u8.load(stream_url)
        num_total_segments = len(stream_obj.segments)
        target_duration = (
            sum([item.duration for item in stream_obj.segments])
            / num_total_segments
        )

        # Pick the segment that contains the requested clip start time.
        # Honor #EXT-X-PROGRAM-DATE-TIME when the uploader emits it: the
        # folder name is only its creation unix time and does not account
        # for the Pi's ffmpeg startup lag (~2 s) before the first audio
        # sample is captured.
        target_unix = float(self.current_clip_start_time)
        clip_duration = float(self.polling_interval_in_seconds)
        segment_start_index = None
        pdt_present = False
        for i, seg in enumerate(stream_obj.segments):
            if seg.program_date_time is None:
                continue
            pdt_present = True
            seg_end_unix = seg.program_date_time.timestamp() + seg.duration
            if seg_end_unix > target_unix:
                segment_start_index = i
                break

        if segment_start_index is None and not pdt_present:
            # No PROGRAM-DATE-TIME tags; fall back to folder-name math.
            segment_start_index = math.ceil(
                datetime_utils.get_difference_between_times_in_seconds(
                    self.current_clip_start_time, current_folder
                )
                / target_duration
            )

        needs_advance = segment_start_index is None

        # Compute the trim offset within the first segment and how many
        # segments cover (trim + requested clip duration). Downloading
        # one extra segment when the clip spans a boundary is required
        # because ffmpeg will skip start_trim seconds at the output.
        start_trim = 0.0
        picked_pdt_ts = None
        if not needs_advance:
            picked_pdt = stream_obj.segments[segment_start_index].program_date_time
            if picked_pdt is not None:
                picked_pdt_ts = picked_pdt.timestamp()
                start_trim = max(0.0, target_unix - picked_pdt_ts)
            num_segs_needed = math.ceil(
                (start_trim + clip_duration) / target_duration
            )
            segment_end_index = segment_start_index + num_segs_needed
            needs_advance = segment_end_index > num_total_segments

        if needs_advance:
            # Move to the next folder. If there is no next folder, mark
            # the stream as over instead of indexing past the end of
            # valid_folders.
            self.current_folder_index += 1
            if self.current_folder_index >= len(self.valid_folders):
                self.is_end_of_stream = True
                self.current_clip_start_time = self.end_unix_time
                return None, None, None
            next_folder_start = float(
                self.valid_folders[self.current_folder_index]
            )
            self.current_clip_start_time = max(
                float(self.current_clip_start_time), next_folder_start
            )
            return None, None, None

        # The WAV's actual start wall-clock time: normally the user's
        # requested target (we trim the segment lead-in to hit it to the
        # sample). In the rare case the request precedes the picked
        # segment (asked before the stream existed), start at the
        # segment's own PDT instead.
        if picked_pdt_ts is not None and target_unix < picked_pdt_ts:
            wav_start_unix = picked_pdt_ts
            start_trim = 0.0
        else:
            wav_start_unix = target_unix

        self.current_clip_start_time = wav_start_unix
        (
            clipname,
            clip_start_time,
        ) = datetime_utils.get_clip_name_from_unix_time(
            self.folder_name.replace("_", "-"), self.current_clip_start_time
        )

        # Advance by exactly the clip duration from this clip's start so
        # consecutive WAVs tile the user's grid with no overlap or gap.
        self.current_clip_start_time = wav_start_unix + clip_duration

        # Create tmp path to hold .ts segments
        with TemporaryDirectory() as tmp_path:
            os.makedirs(tmp_path, exist_ok=True)

            file_names = []
            for i in range(segment_start_index, segment_end_index):
                audio_segment = stream_obj.segments[i]
                base_path = audio_segment.base_uri
                file_name = audio_segment.uri
                audio_url = base_path + file_name
                try:
                    scraper.download_from_url(audio_url, tmp_path)
                    file_names.append(file_name)
                except Exception:
                    print("Skipping", audio_url, ": error.")

            # concatentate all .ts files
            hls_file = os.path.join(tmp_path, Path(clipname + ".ts"))
            with open(hls_file, "wb") as wfd:
                for f in file_names:
                    with open(os.path.join(tmp_path, f), "rb") as fd:
                        shutil.copyfileobj(fd, wfd)

            # Decode the concatenated .ts and write WAV, trimming the
            # lead-in from the first segment with output-side -ss (sample
            # accurate for audio) and capping length with -t so the WAV
            # covers exactly [wav_start_unix, wav_start_unix + clip_duration).
            audio_file = clipname + ".wav"
            wav_file_path = os.path.join(self.wav_dir, audio_file)
            stream = ffmpeg.input(os.path.join(tmp_path, Path(hls_file)))
            stream = ffmpeg.output(
                stream, wav_file_path, ss=start_trim, t=clip_duration
            )
            ffmpeg.run(
                stream, overwrite_output=self.overwrite_output, quiet=False
            )

        # If we're in demo mode, we need to fake timestamps to make it seem
        # like the date range is real-time
        if current_clip_name:
            clipname, _ = get_readable_clipname(
                self.folder_name.replace("_", "-"), current_clip_name
            )

            # rename wav file
            full_new_clip_path = os.path.join(self.wav_dir, clipname + ".wav")
            os.rename(wav_file_path, full_new_clip_path)
            wav_file_path = full_new_clip_path

            # change clip_start_time - this has to be in UTC so that the email
            # can be in PDT
            clip_start_time = current_clip_name.isoformat() + "Z"

        # Get new index
        return wav_file_path, clip_start_time, current_clip_name

    def is_stream_over(self):
        # returns true or false based on whether the stream is over
        if self.is_end_of_stream:
            return True
        return int(self.current_clip_start_time) >= int(self.end_unix_time)