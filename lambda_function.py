import os
import io
import json
import uuid
import boto3
import base64
import logging
import pinecone
from datetime import datetime
from openai import OpenAI
from langchain.embeddings import OpenAIEmbeddings

# Initialize
s3 = boto3.client('s3')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# General
delete_s3_obj = os.environ.get('DELETE_S3_OBJ', 'False').lower() == 'true'

# API keys
openai_api_key = os.environ.get('OPENAI_API_KEY')
openai_client = OpenAI(api_key=openai_api_key)
embeddings_model = OpenAIEmbeddings(openai_api_key=openai_api_key)

pinecone_api_key = os.environ.get('PINECONE_API_KEY')
pinecone_env_key = os.environ.get('PINECONE_ENV_KEY')
pinecone_index_name = os.environ.get('PINECONE_INDEX_NAME')
pinecone.init(api_key=pinecone_api_key, environment=pinecone_env_key)
index = pinecone.Index(pinecone_index_name)

# System Prompts
whisper_prompt = str(os.environ.get('WHISPER_PROMPT'))
clean_system_prompt = str(os.environ.get('CLEAN_SYSTEM_PROMPT'))
facts_system_prompt = str(os.environ.get('FACTS_SYSTEM_PROMPT'))
upsert_check_system_prompt = str(os.environ.get('UPSERT_CHECK_SYSTEM_PROMPT'))
speaker_label_system_prompt = str(os.environ.get('SPEAKER_LABEL_SYSTEM_PROMPT'))

def update_metadata_type(metadata, text):
    document = metadata

    document['text'] = str(text)
    if 'source' in metadata:
        document['source'] = str(metadata['source'])
    
    if 'systemTime' in metadata:
        document['systemTime'] = int(metadata['systemTime'])
    if 'currenttimeformattedstring' in metadata:
        document['currenttimeformattedstring'] = str(datetime.strptime(metadata['currenttimeformattedstring'], '%a %d/%m/%y %H:%M'))
    if 'day' in metadata:
        document['day'] = int(metadata['day'])
    if 'month' in metadata:
        document['month'] = int(metadata['month'])
    if 'year' in metadata:
        document['year'] = int(metadata['year'])
    if 'hours' in metadata:
        document['hours'] = int(metadata['hours'])
    if 'minutes' in metadata:
        document['minutes'] = int(metadata['minutes'])

    if 'address' in metadata:
        document['address'] = str(metadata['address'])
    if 'longitude' in metadata:
        document['longitude'] = float(metadata['longitude'])
    if 'latitude' in metadata:
        document['latitude'] = float(metadata['latitude'])

    if 'batterylevel' in metadata:
        document['batterylevel'] = int(metadata['batterylevel'])

    if 'firstweatherdescription' in metadata:
        document['firstweatherdescription'] = str(metadata['firstweatherdescription'])
    if 'cloudall' in metadata:
        document['cloudall'] = int(metadata['cloudall'])
    if 'feelslike' in metadata:
        document['feelslike'] = float(metadata['feelslike'])
    if 'humidity' in metadata:
        document['humidity'] = int(metadata['humidity'])
    if 'windspeed' in metadata:
        document['windspeed'] = float(metadata['windspeed'])

    return document

def start_processing(event):
    # Get bucket name and object key from the event
    bucket_name = event['Records'][0]['s3']['bucket']['name']
    object_key = event['Records'][0]['s3']['object']['key']
    logger.info(f"Event: {bucket_name} - {object_key}\n")
    
    # Get the filename and download path
    filename = object_key.split("/")[-1]
    download_path = os.path.join('/tmp', filename)
    logger.info(f"filename: {filename} - download_path: {download_path}\n")
    
    # Download the file from S3
    s3.download_file(bucket_name, object_key, download_path)
    
    # Retrieve metadata for the object
    response = s3.head_object(Bucket=bucket_name, Key=object_key)
    metadata = response.get('Metadata', {})
    logger.info(f"Metadata: {metadata}\n")
    
    with open(download_path, 'rb') as file_obj:
        raw_transcript = whisper(whisper_prompt, file_obj)
        clean_transcript = gpt("gpt-4-1106-preview", clean_system_prompt, raw_transcript)
        speaker_label_transcript = gpt("gpt-4-1106-preview", speaker_label_system_prompt, clean_transcript)
   
        if(speaker_label_transcript != '.'):
            vector(speaker_label_transcript, metadata)
    
    # Delete the S3 object after processing is complete
    if delete_s3_obj:
        s3.delete_object(Bucket=bucket_name, Key=object_key)
        logger.info(f"Deleted S3 object: {bucket_name}/{object_key}")
    
def whisper(system_prompt, file_content):
    response = openai_client.audio.transcriptions.create(
        model = "whisper-1", 
        file = file_content, 
        language = "en",
        prompt = system_prompt
    )
    transcript_text = response.text

    logger.info(f"Whisper API Response: {transcript_text}\n")

    return transcript_text
def gpt(modelName, system_prompt, user_text):
    response = openai_client.chat.completions.create(
        model=modelName,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
    )
    assitant_text = response.choices[0].message.content

    logger.info(f"GPT API Response: {assitant_text}\n")

    return assitant_text
def vector(text, metadata):
    # Initialize the Pinecone client
    embedding = embeddings_model.embed_documents([text])
    updated_metadata = update_metadata_type(metadata, text)
    index.upsert([
        (
            str(uuid.uuid4()),  # Convert UUID to string
            embedding[0],
            updated_metadata
        ),
    ])

    logger.info(f"Upserted into DB successfully!\n")

def handler(event, context):
    try:
        logger.info(f'started lambda_handler\n\n')

        # Running on AWS Lambda
        if context:
            start_processing(event)
        # Running locally
        else:
            local_file_path = r'.\Data\recordings\recording_206419037.m4a'
            object_key = r'recordings\recording_206419037.m4a'

            with open(local_file_path, 'rb') as file_obj:
                raw_transcript = whisper(whisper_prompt, file_obj)
                clean_transcript = gpt("gpt-4-1106-preview", clean_system_prompt, raw_transcript)
                speaker_label_transcript = gpt("gpt-4-1106-preview", speaker_label_system_prompt, clean_transcript)
        
                if(speaker_label_transcript != '.'):
                    vector(speaker_label_transcript, metadata)
        return {
            'statusCode': 200,
            'body': json.dumps('Processing complete')
        }

    except Exception as e: 
        logger.error(f'Error: \n{e}\n\n')
        logger.error("Stack Trace:", exc_info=True)
        
        return {
            'statusCode': 400,
            'body': f'Error! {e}'
        }

if __name__ == '__main__':
    # Dummy context
    test_context = None
    # Dummy event
    test_event = {
        'Records': [{
            's3': {
                'bucket': {'name': 'mia-audiofiles'},
                'object': {'key': 'recording_155994629.m4a'}
            }
        }]
    }

    # Call the lambda handler
    response = handler(test_event, test_context)
    print(response)