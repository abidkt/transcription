import json
import sys
import os
import requests
import subprocess
import re

# Check if both a folder path and API base URL were provided as command line arguments
if len(sys.argv) < 3:
    print("Please provide a folder path and an API base URL as command line arguments.")
    sys.exit(1)

folder_path = sys.argv[1]
api_base_url = sys.argv[2]

checkPointsFile = os.path.join(folder_path, 'data.json')

if not os.path.exists(checkPointsFile):
    print("Check points file not exists")
    sys.exit(1)

checkPointsFile = open (checkPointsFile, "r")
checkPointsData = json.loads(checkPointsFile.read())

# Iterating through the json list
checkPointsString = ''
for i, checkPoint in enumerate(checkPointsData['checkPoints']):
    checkPointsString += "Check point " + str(i+1) + ": " + checkPoint['text']+"\n"
# Closing file
checkPointsFile.close()

transcriptionText = ''
# Find the text file with the same name as the folder
for audio_path in os.listdir(folder_path):
    if not os.path.isdir(os.path.join(folder_path, audio_path)):
        continue

    txt_file_name = audio_path + '.txt'
    txt_file_path = os.path.join(folder_path, audio_path, txt_file_name)

    if not os.path.exists(txt_file_path):
        print(f"No text file found with the name '{txt_file_name}' in the provided folder: {folder_path}")
        sys.exit(1)

    # Read the text file
    with open(txt_file_path, 'r', encoding='utf-8') as file:
        transcriptionText += file.read() + "\n"

promptText = f"""Analyse the sale transcription below for the given check points. Also give me a summary of the conversation.
The check points are:
{checkPointsString}
The transcription is as follows
{transcriptionText}

```json"""

print(promptText)

# JSON payload
payload = {
    "model": "llama3",
    "prompt": promptText,
    "stream": False,
    "keep_alive": "5s"
}

# Try to send a GET request to check if the API is running
try:
    api_response = requests.get(api_base_url, timeout=5)
    if api_response.status_code == 200 and api_response.text == "Ollama is running":
        print("API endpoint is running.")
    else:
        print("Invalid response from API endpoint")
        sys.exit(1)
except requests.ConnectionError as e:
    print("Ollama connection error: API endpoint down. Moving along.")
    sys.exit(1)
except requests.Timeout as e:
    print("Ollama request timed out: API endpoint down. Moving along..")
    sys.exit(1)
except Exception as e:
    print("Error connecting to the API endpoint: {}".format(str(e)))
    sys.exit(1)

# Send the POST request with the base URL and path
request_url = api_base_url + '/api/generate'
response = None
try:
    response = requests.post(request_url, json=payload)
except Exception as e:
    print("Error sending request to API endpoint: {}".format(e))
    sys.exit(1)

# Check if the request was successful and print or exit accordingly
if response is not None and response.status_code == 200:
    # Parse the JSON response
    json_data = response.json()
    json_data['prompt'] = promptText

    # Write the summary to a file named summary.txt in the same folder
    with open(os.path.join(folder_path, 'summary.json'), 'w', encoding='utf-8') as summary_file:
        summary_file.write(json.dumps(json_data))

    if checkPointsData['returnUrl']:
        headers = {'Authorization': "Bearer " + os.environ.get('SALESDOCK_AUTHORIZATION')}
        requests.post(checkPointsData['returnUrl'], json=json_data, headers=headers)

else:
    if response is not None:
        print("Request failed with status code:", response.status_code)
        print("Error message from API:", json_data.get('error', ''))
        sys.exit(1)
