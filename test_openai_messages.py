import os, time, traceback, io
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url='https://api.openai.com/v1', default_headers={'OpenAI-Beta': 'assistants=v2'})

try:
    thread = client.beta.threads.create()
    print('Thread ID:', thread.id)
    client.beta.threads.messages.create(thread_id=thread.id, role='user', content='Hello test')
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id='asst_M65nFsVKjQRamCQrfHThTeJt')
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if run.status == 'completed':
            break
        elif run.status in ['failed', 'cancelled', 'expired']:
            raise RuntimeError(f'Run failed: {run.status}')
        time.sleep(1)
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    print('Received messages count:', len(messages.data))
    for m in messages.data:
        print(m.role, [c.text.value if c.type=='text' else None for c in m.content])
except Exception as e:
    print('Error:', e)
    traceback.print_exc() 