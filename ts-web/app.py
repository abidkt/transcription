import time
import os
import shutil
import requests
import json
# import chromadb
import ollama
from flask import Flask, render_template, request, jsonify, session, send_file, g
from werkzeug.utils import secure_filename
from flask_http_middleware import MiddlewareManager
from middleware import AccessMiddleware, MetricsMiddleware, SecureRoutersMiddleware
from marshmallow import Schema, fields, validate, ValidationError
from datetime import datetime
from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
from typing import List
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain.chains import SequentialChain, LLMChain
from langchain_core.runnables import RunnableSequence, RunnableParallel

app = Flask(__name__)

app.wsgi_app = MiddlewareManager(app)

publicRoutes = ['/prompt']

app.wsgi_app.add_middleware(AccessMiddleware, publicRoutes=publicRoutes)
app.wsgi_app.add_middleware(MetricsMiddleware)
        
# Use the TS_WEB_SECRET_KEY environment variable as the secret key, and the fallback
app.secret_key = os.environ.get('TS_WEB_SECRET_KEY', 'some_secret_key')
ollamaIp = os.environ.get('OLLAMA_ENDPOINT_IP', '172.30.1.3')
salesdockUrl = os.environ.get('SALESDOCK_URL', 'https://app.salesdock.nl')

TRANSCRIBED_FOLDER = '/transcriptionstream/transcribed'
UPLOAD_FOLDER = '/transcriptionstream/incoming'
ALLOWED_EXTENSIONS = set(['mp3', 'wav', 'ogg', 'flac'])
MIME_TYPES = dict({
    "audio/mpeg": "mp3",
    "binary/octet-stream": "mp3",
    "audio/wav": "wav"
})

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

session_start_time = datetime.now()

def sendPrompt(prompt):
    global llm
    response = llm.invoke(prompt)
    return response

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_extension(content_type):
    for key, val in MIME_TYPES.items():
        if key == content_type:
            return val
    return False

class AudioSchema(Schema):
    audioId = fields.Integer(required=True)
    url = fields.Str(required=True)
    class Meta:
        strict = True

class AudioAnalysisSchema(Schema):
    saleId = fields.Integer(required=True)
    rowId = fields.Integer(required=True)
    audios = fields.Nested(AudioSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)
    returnHook = fields.Str(required=True)

class CheckPointSchema(Schema):
    id = fields.Integer(required=True)
    question = fields.Str(required=True)
    description = fields.Str(required=True)
    weightage = fields.Integer()
    class Meta:
        strict = True

# class TranscriptionSchema(Schema):
#     start_time = fields.Integer(required=True)
#     end_time = fields.Integer(required=True)
#     speaker = fields.Str(required=True)
#     text = fields.Str()
#
# class TranscriptionsSchema(Schema):
#     transcription = fields.Nested(TranscriptionSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)

class GenerateSchema(Schema):
    transcription = fields.Str(required=True)
    checkPoints = fields.Nested(CheckPointSchema, required=True, validate=validate.Length(min=1, error='Field may not be an empty list'), many=True)
    model = fields.Str(required=True)
    options = fields.Dict()
    additionalPrompts = fields.Str(required=False)

@app.before_request
def before_request():
    g.start_time = datetime.now()

@app.route('/')
def index():
    # Reset the session variable on page load
    session['alerted_folders'] = []
    session['session_start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    folder_paths = [os.path.join(TRANSCRIBED_FOLDER, f) for f in os.listdir(TRANSCRIBED_FOLDER) if os.path.isdir(os.path.join(TRANSCRIBED_FOLDER, f))]
    
    # Filter folders to only include those containing an .srt file
    valid_folders = []
    for folder in folder_paths:
        files = os.listdir(folder)
        if any(file.endswith('.srt') for file in files):
            valid_folders.append(os.path.basename(folder))
    
    sorted_folders = sorted(valid_folders, key=lambda s: s.lower())  # Sorting by name in ascending order, case-insensitive
    return jsonify({"transcriptions": sorted_folders})

@app.route('/upload', methods=['POST'])
def upload_audio():
    request_data = request.json
    schema = AudioAnalysisSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

#     headers = {'Authorization': "Bearer " + os.environ.get('SALESDOCK_AUTHORIZATION')}
    headers = {}

    folderPath = os.path.join(app.config['UPLOAD_FOLDER'], 'diarize', str(request_data['rowId']))
    if (os.path.exists(folderPath)):
        shutil.rmtree(folderPath)

    os.mkdir(folderPath)
    for audio in request_data['audios']:
#         audioUrl = salesdockUrl + '/' + audio['url']
        audioUrl = audio['url']
        response = requests.get(audioUrl, headers=headers, verify=False)
        if response.status_code == requests.codes.ok:
            contentType = response.headers.get('content-type')
            extension = get_extension(contentType)
            if (extension == False):
                raise Exception('Invalid extension for a file')

            filename = secure_filename(str(audio['audioId']) + '.' + extension)
            with open(os.path.join(folderPath, filename), mode="wb") as file:
                file.write(response.content)
        else:
            raise Exception('Download from url failed for ' + audioUrl)

    with open(os.path.join(folderPath, secure_filename('data.json')), mode="w") as file:
        json.dump(request_data, file)
    return jsonify(success=True, message="File saved successfully"), 200

@app.route('/transcription/<path:folder>', methods=['GET'])
def summary(folder):
    folderPath = os.path.join(TRANSCRIBED_FOLDER, folder)
    if not os.path.exists(folderPath):
        return jsonify(error='Folder does not exist'), 404

    audios = []
    for dir in os.listdir(folderPath):
        subDir = os.path.join(folderPath, dir)
        if os.path.isdir(subDir):
            transcriptionFile = os.path.join(subDir, dir + '.txt')
            transcriptionFileContents = False
            if os.path.isfile(transcriptionFile):
                with open(transcriptionFile) as f:
                    transcriptionFileContents = f.read()

            transcriptionJson = os.path.join(subDir, dir + '.json')
            transcriptionJsonContents = False
            if os.path.isfile(transcriptionJson):
                with open(transcriptionJson, encoding='utf-8-sig') as f:
                    transcriptionJsonContents = json.load(f)

            audios.append({'id': dir, 'text': transcriptionFileContents, 'json': transcriptionJsonContents})

    return jsonify(success=True, audios=audios)

@app.route('/generate', methods=['POST'])
def generate():
    request_data = request.json
    schema = GenerateSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    ollamaUrl = 'http://' + ollamaIp + ':11434'

    apiResponse = requests.get(ollamaUrl, timeout=5)
    if apiResponse.status_code != 200 or apiResponse.text != "Ollama is running":
        raise Exception('Api is not working')

    class CheckPointResponse(BaseModel):
        question: str = Field(description="question itself in the prompt for reference")
        compliant: str = Field(description="the check point is compliant or not, yes, if compliant. no, if not compliant, na, if not mentioned in transcript")
        score: str = Field(description="score of the check")
        summary: str = Field(description="summary of the check")

    modelName = request_data["model"]

    transcription = request_data['transcription']
    checkPoints = request_data['checkPoints']
    additionalPrompts = request_data['additionalPrompts']
    systemPrompt = 'You are sale analyst. You check sale conversions and analyze. Returns answers in the given JSON format'
    model = Ollama(model=modelName, base_url=ollamaUrl, temperature=0, verbose=True, top_k=0, system=systemPrompt, format="json")
    parser = PydanticOutputParser(pydantic_object=CheckPointResponse)
    outputFormat = json.dumps({"id":"check point number","question":"check point heading in the prompt","compliant":"check is compliant or not","score":"score for the check point if available, otherwise NA","summary": "brief summary of the check point"})

    tasks = {}
    for checkPoint in checkPoints:
        query = "Answer the check point based on the conversation transcript in the below format.\n{format_instructions}\nConversation transcript:\n{transcription}\nFormat:\n{format}\nPrompt:\nCheck the agent discussed the below check point and assign a score of 0-5 for the checkpoint based on how well the agent performed in that area. Briefly summarise the agent's strengths and weaknesses in the checkpoint.\nQuestion: {question}\nQuestion description: \n{questionDescription}"
        question_chain = (
            PromptTemplate(
                template=query,
                input_variables=["transcription"],
                partial_variables={"format": outputFormat, "format_instructions": parser.get_format_instructions(), "question": checkPoint['question'], "questionDescription": checkPoint['description']},
            )
            | model
            | parser
        )

        tasks["item-" + str(checkPoint['id'])] = question_chain

    class AdditionalInfoResponse(BaseModel):
        summary: str = Field(description="transcriptions summary")
        score: int = Field(description="score of the conversation")
        additionalInfo: str = Field(description="additional info requested as string")

    additionalInfoParser = PydanticOutputParser(pydantic_object=AdditionalInfoResponse)
    additionalInfoFormat = json.dumps({"summary":"transcriptions summary", "score":"score of the conversation in integer out of 100", "additionalInfo": "additional info requested as string"})
    query = "Answer the prompt based on the conversation transcript in the below format\n{format_instructions}\nConversation transcript:\n{transcription}\n{format}\nPrompt:\n{additionalPrompts}"
    question_chain = (
        PromptTemplate(
            template=query,
            input_variables=["transcription"],
            partial_variables={"format": additionalInfoFormat, "format_instructions": additionalInfoParser.get_format_instructions(), "additionalPrompts": request_data['additionalPrompts']},
        )
        | model
        | additionalInfoParser
    )

    tasks["item-additional"] = question_chain

    multi_question_chain = RunnableParallel(tasks)

    output = multi_question_chain.invoke({
        "transcription": transcription
    })

    jsonData = {}
    for item in output:
        jsonData[item] = json.loads(output[item].json())

    elapsedTime = datetime.now() - g.start_time
    return jsonify(success=True, data=jsonData, time=elapsedTime.total_seconds()), 200


@app.route('/generate2', methods=['POST'])
def generate2():
    request_data = request.json
    schema = GenerateSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    ollamaUrl = 'http://' + ollamaIp + ':11434'

    apiResponse = requests.get(ollamaUrl, timeout=5)
    if apiResponse.status_code != 200 or apiResponse.text != "Ollama is running":
        raise Exception('Api is not working')


    client = chromadb.Client()
    collection = client.create_collection(name="transcription")

    transcription = request_data['transcription']
    transcription = transcription.splitlines()

    for i, d in enumerate(transcription):
        if (d == ''):
            continue
        response = ollama.embeddings(model="mxbai-embed-large", prompt=d)
        embedding = response["embedding"]
        collection.add(
            ids=[str(i)],
            embeddings=[embedding],
            documents=[d]
        )

    class CheckPointResponse(BaseModel):
        question: str = Field(description="question itself in the prompt for reference")
        compliant: str = Field(description="the check point is compliant or not, yes, if compliant. no, if not compliant, na, if not mentioned in transcript")
        score: str = Field(description="score of the check")
        summary: str = Field(description="summary of the check")

    modelName = request_data["model"]
    checkPoints = request_data['checkPoints']
    systemPrompt = 'You are sale analyst. You check sale conversions and analyze. Returns answers in the given JSON format'
    model = Ollama(model=modelName, base_url=ollamaUrl, temperature=0, verbose=True, top_k=0, system=systemPrompt, format="json")
    parser = PydanticOutputParser(pydantic_object=CheckPointResponse)
    outputFormat = json.dumps({"id":"check point number","question":"check point heading in the prompt","compliant":"check is compliant or not","score":"score for the check point if available, otherwise NA","summary": "brief summary of the check point"})

    tasks = {}
    for checkPoint in checkPoints:
        prompt = "Check the agent discussed the below check point and assign a score of 0-5 for the checkpoint based on how well the agent performed in that area. Briefly summarise the agent's strengths and weaknesses in the checkpoint.\nQuestion: " +  checkPoint['question'] + "\nQuestion description: \n" +  checkPoint['description']
        response = ollama.embeddings(
            prompt=prompt,
            model="mxbai-embed-large"
        )
        results = collection.query(
            query_embeddings=[response["embedding"]],
            n_results=1
        )
        data = results['documents'][0][0]

        question_chain = (
            PromptTemplate(
                template="Answer the check point based on the conversation transcript in the below format.\n{format_instructions}\nFormat:\n{format}\nData: {data}\nPrompt:\nCheck the agent discussed the below check point and assign a score of 0-5 for the checkpoint based on how well the agent performed in that area. Briefly summarise the agent's strengths and weaknesses in the checkpoint.\nQuestion: {question}\nQuestion description: \n{questionDescription}",
                partial_variables={"data": data, "format": outputFormat, "format_instructions": parser.get_format_instructions(), "question": checkPoint['question'], "questionDescription": checkPoint['description']},
            )
            | model
            | parser
        )
        tasks["item-" + str(checkPoint['id'])] = question_chain

    multi_question_chain = RunnableParallel(tasks)

    output = multi_question_chain.invoke({})

    jsonData = {}
    for item in output:
        jsonData[item] = json.loads(output[item].json())

    return jsonify(success=True, data=jsonData), 200

@app.route('/generate3', methods=['POST'])
def generate3():
    request_data = request.json
    schema = GenerateSchema()
    try:
        result = schema.load(request_data)
    except ValidationError as err:
        return jsonify(success=False, errors=err.messages), 400

    payload = {
        "model": request_data["model"],
        "prompt": request_data['prompt'],
        "stream": False,
        "keep_alive": "5s",
        "format": "json",
        "system": 'You are sale analyst. You check sale conversions and give scores and summary of the conversation in json format',
        "options" : request_data['options']
    }

    apiResponse = requests.get(ollamaUrl, timeout=5)
    if apiResponse.status_code != 200 or apiResponse.text != "Ollama is running":
        raise Exception('Api is not working')

    requestUrl = ollamaUrl + '/api/generate'
    response = None
    try:
        response = requests.post(requestUrl, json=payload)
    except Exception as e:
        raise Exception("Error sending request to API endpoint: {}".format(e))

    json_data = response.json()

    if response is not None and response.status_code == 200:
        json_data = response.json()
        return jsonify(success=True, data=json_data), 200

    return jsonify(success=False, message=response.error), 200

@app.route('/delete/<path:folder>', methods=['DELETE'])
def delete_folder(folder):
    folder_path = os.path.join(TRANSCRIBED_FOLDER, folder)
    if not os.path.exists(folder_path):
        return jsonify(success=False, error='Folder does not exist'), 404
    
    try:
        shutil.rmtree(folder_path)
        return jsonify(success=True)
    except Exception as e:
        print(f"Error deleting folder: {e}")
        return jsonify(success=False, error='Failed to delete folder'), 500

@app.route('/prompt', methods=['GET', 'POST'])
def prompt():
    class MyResponse(BaseModel):
    #     summary: str = Field(description="total summary of the transcription")
    #     additionalInfo: str = Field(description="additional info requested")
        totalScore: int = Field(description="total score")
        checkPoints: List[CheckPointResponse] = []

    checkPoints = [
        {
            "id": 1,
            "question": "Maakt de medewerker geen gebruik van misleidende argumentatie?",
            "description": "De medewerker maakt geen gebruik van misleidende argumenten, in de breedste zin van het woord. Dit betekent dat het geven van onjuiste informatie, het achterhouden van belangrijke informatie en het presenteren van informatie die de medewerker niet kan weten, niet is toegestaan.Bijvoorbeeld, de medewerker mag niet suggereren dat er voor de consument niets verandert omdat hij bij dezelfde netbeheerder blijft of deel uitmaakt van hetzelfde collectief. De medewerker moet duidelijk maken dat de netbeheerder verantwoordelijk is voor het transport van stroom en gas, maar niet voor de levering ervan. Negatieve uitspraken over andere leveranciers of het verspreiden van onjuiste informatie over de markt, zoals \"de markt is duur omdat X het heeft overgenomen\", zijn niet toegestaan.Er is sprake van misleidende argumentatie als de consument wordt misleid door de medewerker. Dit gebeurt wanneer de medewerker onjuiste informatie verstrekt. Een voorbeeld hiervan is wanneer de medewerker doet alsof hij de huidige tarieven van de consument kan zien en zegt: \"Uw huidige tarief is 22 cent en dit kan verlaagd worden naar 15 cent.\" Dit is misleidend en moet worden afgekeurd.De argumentatie is ook onjuist als de medewerker beweert dat er fouten zijn gemaakt bij de vorige overstap van de consument, waardoor deze een hoge eindafrekening heeft en beter kan overstappen. Als de medewerker suggereert dat de consument is 'opgelicht', wordt dit afgekeurd. Ook als de medewerker doet alsof hij de klant al kent, eerder zaken voor de klant heeft geregeld en de klant aangeeft dat dit niet het geval is, wordt dit als fout beschouwd.Als de medewerker zegt dat hij belt in verband met het huidige contract van de klant bij bijvoorbeeld Energiedirect, wordt dit ook afgekeurd."
        },{
            "id": 2,
            "question": "Benoemt de medewerker de handelsnaam van de adverteerder?",
            "description": "Aan het begin van het gesprek moet de agent duidelijk de naam van de organisatie noemen van waaruit het gesprek wordt gevoerd. Dit wordt afgekeurd in de volgende gevallen:- Als wordt aangegeven dat de werving uit naam van ENGIE gebeurt terwijl dit niet het geval is.- Als de handelsnaam niet wordt vermeld.- Als wordt gezegd dat de agent namens de netbeheerder belt."
        },{
            "id": 3,
            "question": "Heeft de medewerker benoemd dat het gaat om het aanbieden van een nieuwenergiecontract?",
            "description": "Aan het begin van het gesprek moet de consument weten dat het gesprek gaat over een aanbod voor de levering van gas en/of stroom. Het is belangrijk dat termen zoals aanbod, voorstel, aanbieding, maatwerk of propositie worden gebruikt. Het is niet voldoende als de agent alleen spreekt over een tariefswijziging, een wijziging in het contract, of alleen het woord korting gebruikt.Verdieping: Als blijkt dat de agent de consument bijvoorbeeld vijf minuten eerder al heeft geïnformeerd over het aanbod, hoeft de agent het woord 'aanbod' niet opnieuw te gebruiken. Het moet echter wel duidelijk zijn dat de consument begrijpt wat het doel van dit gesprek is.De agent moet duidelijk aangeven dat het om een nieuw aanbod gaat. Als de agent zegt dat hij de consument een jaar geleden een aanbod heeft gedaan en nu weer contact opneemt, is dit niet duidelijk genoeg. Het moet expliciet zijn dat er nu een nieuw aanbod wordt gedaan. Anders wordt dit afgekeurd."
        },{
            "id": 4,
            "question": "Beëindigt de medewerker het gesprek als er sprake is van een kwetsbare consument?",
            "description": "Een kwetsbare consument is iemand die door zijn of haar kwetsbaarheid niet zelfstandig beslissingen kan nemen over het energiecontract. Dit kan bijvoorbeeld iemand zijn die meerdere keren aangeeft het niet te begrijpen, iemand die de taal niet goed spreekt, of iemand die onder bewind staat.Dit criterium is niet relevant als de consument niet kwetsbaar of onervaren is. Het criterium wordt als goed beoordeeld als de agent adequaat rekening houdt met de kwetsbaarheid of onervarenheid van de consument.Voorbeelden van kwetsbare consumenten:Iemand die de Nederlandse taal niet beheerst.Iemand die aangeeft onder bewindvoering te staan.Iemand die ouder is dan 75 jaar.Misbruik van de onkunde of onwetendheid van de consument.Twijfel aan de mentale gezondheid van de consument."
        },{
            "id": 5,
            "question": "Is de verbruiksbepaling c.q. aanbod op maat correct tot stand gekomen?",
            "description": "De agent stelt de benodigde vragen, verstrekt proactief de benodigde informatie en vraagt door bij onduidelijkheden. Een gepersonaliseerd aanbod betekent dat de agent ervoor moet zorgen dat het aanbod aansluit op de persoonlijke situatie van de consument.Voor een correcte uitvoering van de gepersonaliseerde aanbodprocedure, zijn er maximaal drie stappen, in deze volgorde:De agent vraagt of de consument het verbruik weet.Indien nee, vraagt de agent of de consument het verbruik kan opzoeken, bijvoorbeeld via de jaarnota, de online omgeving of app van de huidige leverancier.Indien nee, maakt de agent gebruik van de schattingstool om het verbruik te schatten op basis van de situatie van de consument. De agent moet hierbij minimaal drie inhoudelijke vragen stellen.De agent moet nagaan of er sprake is van terug levering, waarbij de consument zelf elektriciteit opwekt. Dit doet de agent door te vragen of de consument zelf elektriciteit opwekt, bijvoorbeeld via zonnepanelen.Verdieping:De agent heeft een inspanningsverplichting. Eerst vraagt hij naar het daadwerkelijke verbruik van de consument. Als de consument dit niet weet en ook niet bereid is om dit op een later moment op te zoeken, moet de agent een goede inschatting maken van het verbruik. Dit betekent dat hij vragen stelt om een goed beeld van de situatie te krijgen, zoals: hoeveel mensen wonen er in de woning? Wat voor type woning is het? Hoe is de woning geïsoleerd? Wat voor type meter heeft de consument? Minimaal drie parameters moeten worden gebruikt. Indien dit niet gebeurt, wordt dit afgekeurd.Het register dat het gemiddelde verbruik van de afgelopen jaren bijhoudt (CAR / CER) is niet beschikbaar voor de partners. Als de agent beweert dit register te hebben geraadpleegd, is dit fout. Ook het zeggen dat hij het contract van de consument heeft geraadpleegd, wordt afgekeurd omdat de agent dit niet kan inzien.Als de agent een verbruik noemt zonder uitleg over hoe dit verbruik is vastgesteld, wordt dit afgekeurd.Als de agent aangeeft dat het verbruik van de consument bekend is in zijn bestand of systeem en dit gebruikt, wordt dit afgekeurd.De consument moet duidelijk weten hoe de medewerker het aanbod heeft samengesteld. Dit doet de medewerker door bijvoorbeeld een controlevraag te stellen: \"Mag ik van dit verbruik uitgaan?\" of door samen te vatten: \"Op basis van X kWh en X m³ kom ik uit op … Zal ik hiervan uitgaan?\""
        },{
            "id": 6,
            "question": "Heeft de medewerker het jaarbedrag genoemd?",
            "description": "De medewerker moet de totale verwachte jaarkosten vermelden. Het is belangrijk dat hij/zij duidelijk maakt dat deze jaarkosten een schatting zijn, bijvoorbeeld door termen te gebruiken zoals verwachtte, geschatte of indicatieve. Dit geeft aan dat de jaarkosten afhankelijk zijn van het daadwerkelijke verbruik van de klant.Let op: Dit mag ook in de samenvatting, zolang het vóór het versturen van het aanbod gebeurt"
        },{
            "id": 7,
            "question": "Heeft de medewerker benoemd dat het jaarbedrag inclusief btw, overheidsheffingenen netbeheerkosten is?",
            "description": "De totale jaarkosten bestaan uit verschillende onderdelen, inclusief componenten waar de energieleverancier geen controle over heeft. De medewerker moet duidelijk maken waaruit deze jaarkosten zijn opgebouwd om een compleet overzicht te geven van de kostenstructuur.De medewerker moet ten minste aangeven dat het jaarbedrag inclusief BTW, overheidsheffingen en netbeheerkosten is. Andere terminologie is ook acceptabel, zoals vaste en variabele leveringskosten, netbeheerkosten, belastingen en toeslagen.Let op: Dit is ook acceptabel als het in de samenvatting of voicelog wordt genoemd, zolang het maar duidelijk wordt vermeld voordat het aanbod wordt verstuurd."
        },{
            "id": 8,
            "question": "Heeft de medewerker het leveringstarief incl. BTW en overheidsheffingen en hetbedrag van de vaste leveringskosten benoemd?",
            "description": "De medewerker moet het all-in tarief met alle decimalen vermelden, evenals de maandelijkse vaste kosten. Dit gebeurt meestal in de samenvatting:De tarieven die bij deze [LOOPTIJD] overeenkomst horen zijn (de getallen niet afronden!):Gas: [X] eurocent per m³Elektriciteit (enkel tarief): [X] eurocent per kWh Elektriciteit (normaal tarief): [X] eurocent per kWh Elektriciteit (dal tarief): [X] eurocent per kWh Alle bovengenoemde tarieven en jaarkosten zijn inclusief belastingen, toeslagen en netbeheerkosten. Deze worden gespecificeerd in de overeenkomst die ik u toestuur. De vaste leveringskosten bedragen [X] euro per maand per product, inclusief BTW"
        },{
            "id": 9,
            "question": "Heeft de medewerker duidelijk de startdatum afgesproken met de consument?",
            "description": "De medewerker dient duidelijk te vermelden wanneer de start van levering plaatsvindt."
        },{
            "id": 10,
            "question": "Heeft de medewerker de consument geïnformeerd over een eventuele boete die de oude leverancier in rekening kan brengen?",
            "description": "De medewerker dient de consument erop te wijzen dat indien de klant vast zit aan een meerjarig contract de huidige energieleverancier een boete in rekening kan brengen. De medewerker dient in ieder geval de klant erop te wijzen dat de huidige leverancier een boete kan opleggen indien de klant beschikt over een nog lopend meerjarig contract. Indien dit vaste contract is afgesloten voor 1/6/2023  geldt de oude boete zoals onderstaand omschreven in voorbeeld 1. Indien dit vaste contract is afgesloten na 1/6/2023 geldt de nieuwe boete welke is omschreven in voorbeeld 2. Voorbeeld 1: Voor energiecontracten die vóór 1 juni 2023 zijn afgesloten, geldt een vaste opzegboete gebaseerd op de resterende looptijd van het contract: Minder dan 1,5 jaar resterend: €50 1,5 tot 2 jaar resterend: €75 2 tot 2,5 jaar resterend: €100 Meer dan 2,5 jaar resterend: €125 Deze bedragen gelden per contract en moeten verdubbeld worden als de consument zowel een gas- als een stroomcontract heeft. Voorbeeld 2 Boete voor Contracten Gesloten na 1 juni 2023 Voor contracten afgesloten na 1 juni 2023 is de opzegboete gebaseerd op het prijsverschil tussen het oude en nieuwe tarief, vermenigvuldigd met het geschatte resterende verbruik. Dit betekent dat de boete kan variëren: Prijsverschil berekenen: Verschil tussen oude en nieuwe tarieven. Resterend verbruik berekenen: Geschatte hoeveelheid energie die nog verbruikt zou worden. Boete berekenen: Prijsverschil x resterend verbruik. Deze methode kan leiden tot hogere boetes als het prijsverschil groot is en er nog veel verbruik te verwachten is."
        },{
            "id": 11,
            "question": "Heeft de medewerker de duur van de overeenkomst gecommuniceerd?",
            "description": "De medewerker dient de duur van de overeenkomst duidelijk te vermelden aan de klant. Dit mag ook benoemd worden in de voicelog."
        },{
            "id": 12,
            "question": "Heeft de medewerker de mogelijkheid tot tussentijdse opzegging benoemd en aangegeven dat in dat geval (geen) opzegkosten van toepassing kan zijn?",
            "description": "Indien het een variabel contract betreft dan is er geen opzegboete van toepassing en kan de klant kosteloos overstappen naar een andere energieleverancier. Indien er sprake is van een vaste looptijd en/of een vast contract met vaste prijzen dan is er een opzegboete van toepassing. De medewerker dient dit in beide gevallen duidelijk aan te geven aan de consument."
        },{
            "id": 13,
            "question": "Heeft de medewerker de consument geïnformeerd over het verlengproces. Overeenkomst met variabele tarieven die maandelijks opzegbaar is?",
            "description": "Indien er sprake is van een overeenkomst met vaste tarieven dient de medewerker bij de klant aan te geven dat na de vaste looptijd de overeenkomst automatisch wordt verlengd voor onbepaalde tijd, tegen de dan geldende variabele tarieven. Indien er sprake is van een variabele overeenkomst is dit geen verplichting en dient de medewerker de klant te wijzen op het feit dat de overeenkomst variabel is en dat de klant op elk willekeurig moment kan overstappen naar een andere energiemaatschappij."
        },{
            "id": 14,
            "question": "Heeft de medewerker meerdere betaalwijzen correct aangeboden?",
            "description": "In het gesprek dient de medewerker de klant minimaal 2 mogelijkheden tot betaling aan te bieden. Dit kan per automatische incasso en/of per factuur c.q. acceptgiro gedaan worden."
        },{
            "id": 15,
            "question": "Heeft de medewerker duidelijk gemaakt dat de klant overstapt naar ENGIE?",
            "description": "De medewerker dient ondubbelzinnig duidelijk te maken dat bij het aanvaarden van de overeenkomst en/of offerte de klant een overstap zal maken naar een andere energieleverancier. Dit criterium is zeer belangrijk en dient scherp nageleefd en gecontroleerd te worden. Indien wordt geconstateerd dat de klant de indruk krijgt dat er niks veranderd en de klant dus geen overstap maakt dient dit punt afgekeurd te worden."
        },{
            "id": 16,
            "question": "Heeft de medewerker duidelijk gemaakt dat de consument door op akkoord te klikken een overeenkomst aangaat?",
            "description": "De medewerker dient de overeenkomst naar de klant te versturen per e-mail of SMS, dit dient hij vooraf ook duidelijk te vermelden aan de klant. Het moet duidelijk zijn dat het een aanbod betreft en dat indien de klant op akkoord klikt er een overeenkomst tot stand komt."
        },{
            "id": 17,
            "question": "Heeft de medewerker op een juiste wijze de wettelijke bedenktijd van 14 dagen benoemd?",
            "description": "De medewerker dient de klant te attenderen op het feit dat de klant na het accorderen van de overeenkomst welke per email of sms is verzonden er een wettelijke bedenktijd geldt van 14 kalenderdagen. Deze overeenkomst kan binnen 14 kalenderdagen telefonisch, schriftelijk en/of via een invulformulier op de website van de betreffende energiemaatschappij worden geannuleerd zonder opgave van reden."
        },{
            "id": 18,
            "question": "Heeft de medewerker het Recht van Verzet aangeboden?",
            "description": "De medewerker dient aan het einde van een positief of negatief gesprek een poging te wagen om het recht van verzet/bezwaar aan te bieden. Uitzondering hierop is wanneer er door de medewerker een concrete terugbelafspraak wordt gemaakt met prospect."
        }
    ]

    if request.method == 'POST':
        ollamaUrl = 'http://' + ollamaIp + ':11434'
        modelName = "llama3"
        transcription = request.form.get('prompt')
        systemPrompt = 'You are sale analyst. You check sale conversions and analyze. Returns answers in the given JSON format'
        model = Ollama(model=modelName, base_url=ollamaUrl, temperature=0, verbose=True, top_k=0, system=systemPrompt, format="json")
        parser = PydanticOutputParser(pydantic_object=CheckPointResponse)
        outputFormat = json.dumps({"id":"check point number","question":"check point heading in the prompt","compliant":"check is compliant or not","score":"score for the check point if available, otherwise NA","summary": "brief summary of the check point"})
        # And a query intented to prompt a language model to populate the data structure.

        tasks = {}
        for checkPoint in checkPoints:
            query = "Answer the check point based on the conversation transcript in the below format.\n{format_instructions}\nConversation transcript:\n{transcription}\nFormat:\n{format}\nPrompt:\nCheck the agent discussed the below check point and assign a score of 0-5 for the checkpoint based on how well the agent performed in that area. Briefly summarise the agent's strengths and weaknesses in the checkpoint.\nQuestion: {question}\nQuestion description: \n{questionDescription}"
            question_chain = (
                PromptTemplate(
                    template=query,
                    input_variables=["transcription"],
                    partial_variables={"format": outputFormat, "format_instructions": parser.get_format_instructions(), "question": checkPoint['question'], "questionDescription": checkPoint['description']},
                )
                | model
                | parser
            )

            tasks["item-" + str(checkPoint['id'])] = question_chain

        multi_question_chain = RunnableParallel(tasks)

        output = multi_question_chain.invoke({
            "transcription": transcription
        })

        jsonData = {}
        for item in output:
            jsonData[item] = json.loads(output[item].json())

        options = request.form.get('options')
        return render_template('prompt.html', message=json.dumps(jsonData), prompt=transcription, model="llama3", options=options)

        chains = []
        outputVariables = []
        for checkPoint in checkPoints:
            query = "Conversation transcript:\n{query}\nPrompt:\nCheck the agent discussed the below check point and assign a score of 0-5 for the checkpoint based on how well the agent performed in that area. Briefly summarise the agent's strengths and weaknesses in the checkpoint. Checkpoint:\n#"+ checkPoint['heading'] +"\n"+checkPoint['description']
            summaryPrompt = PromptTemplate(
                template=query,
                input_variables=["query"],
                partial_variables={"format_instructions": parser.get_format_instructions()},
                output_parser=parser
            )

#             chains.append(LLMChain(llm=llm, prompt=summaryPrompt, output_key="item" + str(checkPoint['id'])))

#             outputVariables.append("item" + str(checkPoint['id']))

#             print(query)
#
#             response = model.invoke(prompt)
#             print(response)

            chain = summaryPrompt | model | parser

            response = chain.invoke({"query": query})
            print(json.dumps(response))

            # Set up a parser + inject instructions into the prompt template.

#         # Create SequentialDocumentsChain
#         overall_chain = RunnableSequence(
#             chains=chains,
#             input_variables=["query"],
#             output_variables=outputVariables,
#             verbose=True
#         )
# #
# #         # Run the chain
# #         results = chain.invoke({"query": query})
# #
#         output = overall_chain.invoke({"query": query})

#         chain = prompt2 | model | parser

#         response = chain.invoke({"query": query})
#         print(json.dumps(response))


        options = request.form.get('options')
        model2 = request.form.get('model')
        optionsJson = json.loads(options)
#
#
#
#         llm = Ollama(model="llama3", base_url=ollamaUrl, temperature="0.0", verbose=True, top_k=0, system=systemPrompt)
#         response = llm.invoke(prompt)
#         output_parser = StructuredOutputParser.from_response_schema(response)
#         print(output_parser)
        return render_template('prompt.html', message=response, prompt=query, model=model2, options=options)

#         stream = ollama.generate(
#             model='llama3',
#             prompt=prompt,
#             stream=True,
#             format="json",
#             system='You are sale analyst. You will check sale conversion transcription(only) against given check points and give summary of the conversation in json format.',
#             options=optionsJson
#         )
#
#         for chunk in stream:
#           print(chunk['response'], end='', flush=True)

        ollamaUrl = 'http://' + ollamaIp + ':11434'
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "5s",
            "format": "json",
            "system": 'You are sale analyst. You check sale conversions and give scores and summary of the conversation in json format',
            "options" : optionsJson
        }

        apiResponse = requests.get(ollamaUrl, timeout=5)
        if apiResponse.status_code != 200 or apiResponse.text != "Ollama is running":
            raise Exception('Api is not working')

        requestUrl = ollamaUrl + '/api/generate'
        response = None
        try:
            response = requests.post(requestUrl, json=payload)
        except Exception as e:
            raise Exception("Error sending request to API endpoint: {}" . format(e))

        json_data = response.json()

        if response is not None and response.status_code == 200:
            json_data = response.json()
            return render_template('prompt.html', message=json_data, prompt=prompt, model=model, options=options)

        return render_template('prompt.html', message=json_data['error'], prompt=prompt, model=model, options=options)

    return render_template('prompt.html')
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
