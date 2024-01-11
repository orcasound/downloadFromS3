#  This Python script can download wav files from a S3 bucket at orcasound.net
#  ts segments in the selected datetime interval are downloaded and converted to wav files

##  Run pip3 install -m requirements.txt     in your code's working directory and virtual environment

## Create a local directory for the wav file(s)

##  Set parameter values:

* wavDir = "/home/val/Documents/12_26_2023_OSfailure/"
* dt_start = dt.datetime(2023, 12, 25, 19, 0)
* dt_stop =  dt.datetime(2023, 12, 26, 16, 0)
* hydro =  Hydrophone['ORCASOUND_LAB']
* wavFileSecs = 24 * 60 * 60

## Then run the script downloadWavs.py
