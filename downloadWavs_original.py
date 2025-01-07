import datetime as dt
# from pipeline import NoiseAnalysisPipeline
# from hydrophone import Hydrophone
from orca_hls_utils.DateRangeHLSStream import DateRangeHLSStream
import datetime as dt
import os
import tempfile
import time
import logging

'''
    Extract ts segments for selected time interval and save as wav files
    Code was extracted from https://github.com/orcasound/ambient-sound-analysis
    The procedure gdownloadWavs was extracted from the students' pipeline
        Note that pipeline.py, hydrophone.py, file_connector.py and acoustic_util.py are stored in this folder
    Thanks go to U W Masters students:  
        Caleb Case
        Mitch Haldeman
        Grant Savage
    Make sure that the needed dependencies are installed  pip install -r requirements.txt    
    Specify directory for the wav files and start/stop datetimes
'''

from enum import Enum
from collections import namedtuple

class Hydrophone(Enum):
    """
    Enum for orcasound hydrophones, including AWS S3 Bucket info
    """

    HPhoneTup = namedtuple("Hydrophone", "name bucket ref_folder save_bucket save_folder")

    BUSH_POINT = HPhoneTup("bush_point", "streaming-orcasound-net", "rpi_bush_point", "acoustic-sandbox", "ambient-sound-analysis/bush_point")
    ORCASOUND_LAB = HPhoneTup("orcasound_lab", "streaming-orcasound-net", "rpi_orcasound_lab", "acoustic-sandbox", "ambient-sound-analysis/orcasound_lab")
    PORT_TOWNSEND = HPhoneTup("port_townsend", "streaming-orcasound-net", "rpi_port_townsend", "acoustic-sandbox", "ambient-sound-analysis/port_townsend")
    SUNSET_BAY = HPhoneTup("sunset_bay", "streaming-orcasound-net", "rpi_sunset_bay", "acoustic-sandbox", "ambient-sound-analysis/sunset_bay")
    SANDBOX = HPhoneTup("sandbox", "acoustic-sandbox", "ambient-sound-analysis", "acoustic-sandbox", "ambient-sound-analysis")


def get_wav_files(wavDir, hydrophone, dt_start, dt_end, max_files=None, wavFileLength_seconds=600, overwrite_output=True):
    wav_folder = wavDir
    stream = DateRangeHLSStream(
        'https://s3-us-west-2.amazonaws.com/' + hydrophone.value.bucket + '/' + hydrophone.value.ref_folder,
        wavFileLength_seconds,
        time.mktime(dt_start.timetuple()),
        time.mktime(dt_end.timetuple()),
        wav_folder,
        overwrite_output
    )
    if len(stream.valid_folders) == 0:
        print("EXITING...")
        return
    while max_files is None and not stream.is_stream_over():
        try:
            wav_file_path, clip_start_time, _ = stream.get_next_clip()
            if clip_start_time is None:
                continue
            start_time = [int(x) for x in clip_start_time.split('_')]
            start_time = dt.datetime(*start_time)
        except FileNotFoundError as fnf_error:
            logging.debug("%s clip failed to download: Error %s", clip_start_time, fnf_error)
            pass


wavDir = "/home/bigbox/Music/"
dt_start = dt.datetime(2023, 12, 26, 14, 30)
dt_stop =  dt.datetime(2023, 12, 26, 16, 0)
hydro =  Hydrophone['ORCASOUND_LAB']
wavFileSecs = 24 * 60 * 60

t1 = dt.datetime.now()
get_wav_files(wavDir,hydro, dt_start, dt_stop, wavFileLength_seconds = min((dt_stop - dt_start).total_seconds(), wavFileSecs))
t2 = dt.datetime.now()
print(f"Elapsed time for this download and conversion is {(t2 - t1).total_seconds()/60:.2f} minutes")