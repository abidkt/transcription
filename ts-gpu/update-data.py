import argparse
import os
import json
import sys
import audioread
from helpers import update_json_data

parser = argparse.ArgumentParser()
parser.add_argument(
    "--destination",
    dest="destination",
    default="",
    help="json path",
)
parser.add_argument(
    "--audio",
    dest="audio",
    default="",
    help="audio path",
)
parser.add_argument(
    "--time",
    dest="time",
    default="",
    help="duration",
)

args = parser.parse_args()

print(args)

if args.destination != '' and args.audio != '' and args.time != '':
    dest = args.destination
    audio = args.audio
    if not os.path.exists(dest) or not os.path.exists(audio):
        print("data/audio file not exists")
        sys.exit(1)

    data = {'transcription_time': args.time, 'file_size': os.path.getsize(audio)}

    with audioread.audio_open(audio) as f:
        data['audio_duration'] = f.duration

    update_json_data(dest, data, 'utf-8-sig')