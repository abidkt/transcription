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

salesdockUrl = os.environ.get('SALESDOCK_URL', 'https://app.salesdock.nl')

folder_path = sys.argv[1]
api_base_url = sys.argv[2]

dataFile = os.path.join(folder_path, 'data.json')

if not os.path.exists(dataFile):
    print("Check points file not exists")
    sys.exit(1)

data = open(dataFile, "r")
dataJson = json.loads(data.read())

# Iterating through the json list
checkPointsString = ''
for i, checkPoint in enumerate(dataJson['checkPoints']):
    checkPointsString += "Check point " + str(i+1) + ": " + checkPoint['text']+"\n"

# default
ollamaModel = "llama3"
if 'model' in dataJson:
    ollamaModel = dataJson['model']

# Closing file
dataFile.close()

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

promptText = f"""Analyse the sale transcription below for the given check points and also give me a summary of the conversation. MUST reply the result as json
The check points are:
{checkPointsString}
The transcription is as follows
{transcriptionText}

"""

# JSON payload
payload = {
    "model": ollamaModel,
    "prompt": promptText,
    "stream": False,
    "keep_alive": "5s",
    "format": "json",
    "temperature": 0
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

    headers = {'Authorization': "Bearer " + os.environ.get('SALESDOCK_AUTHORIZATION')}
    requests.get(salesdockUrl + '/' + dataJson['returnHook'], headers=headers, verify=False)

else:
    if response is not None:
        print("Request failed with status code:", response.status_code)
        print("Error message from API:", json_data.get('error', ''))
        sys.exit(1)
