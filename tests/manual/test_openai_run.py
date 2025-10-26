import os, time
from dotenv import load_dotenv
from openai import OpenAI
import traceback, json
load_dotenv()
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', default_headers={'OpenAI-Beta':'assistants=v2'})
assistant_id = 'asst_M65nFsVKjQRamCQrfHThTeJt'
try:
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role='user', content='If a defender fires a panzerfaust at a tank moving in MPh only visible for 1MP, what is target-based to-hit DRM?')
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant_id)
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        print('status', run.status)
        if run.status == 'completed':
            break
        if run.status == 'failed':
            print('failed, last_error:', run.last_error)
            break
        time.sleep(1)
except Exception as e:
    traceback.print_exc() 