from openai import OpenAI
client = OpenAI()

vector_stores = client.vector_stores.list()
print(vector_stores)