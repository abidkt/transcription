import argparse
import os
import requests
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument(
    "--path",
    dest="path",
    default="",
    help="audio directory",
)

args = parser.parse_args()

if args.path != '':
    path = args.path
    salesdockUrl = os.environ.get('SALESDOCK_URL', 'https://app.salesdock.nl')

    dataFile = os.path.join(path, 'data.json')

    if not os.path.exists(dataFile):
        print("data file not exists")
        sys.exit(1)

    data = open(dataFile, "r")
    dataJson = json.loads(data.read())
    data.close()

    if 'notified' in dataJson and dataJson['notified'] == True:
        print("Already notified")
        sys.exit(1)

    if os.path.exists(os.path.join(path, 'data.json')):
        doNotify = True
        for sub_dir in os.listdir(path):
            sub_path = os.path.join(path, sub_dir)

            # Check if the item is a directory
            if os.path.isdir(sub_path):
                # Check for the presence of any .txt and .srt files in the subdirectory
                txt_files = [file for file in os.listdir(sub_path) if file.endswith('.txt')]
                srt_exists = any(file.endswith('.srt') for file in os.listdir(sub_path))

                # If .txt and .srt files exist, check for summary.txt
                if not txt_files or not srt_exists:
                    doNotify = False

        if doNotify:
            hookUrl = salesdockUrl + '/' + dataJson['returnHook']
            print(f"Pinging hook {hookUrl}")
            headers = {'Authorization': "Bearer "}
            result = requests.get(hookUrl, headers=headers, verify=False)
            if result.status_code == requests.codes.ok:
                dataJson['notified'] = True
                with open(dataFile, 'w', encoding='utf-8') as dataFile:
                    dataFile.write(json.dumps(dataJson))
