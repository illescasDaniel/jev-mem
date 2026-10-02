import time
from dotenv import load_dotenv
from typesafe_sdk import Noul, TypeSafeClient

load_dotenv()
client = TypeSafeClient()
state = {
    "new_memory": {"id": "m2", "content": "Mira: I bought a new bicycle yesterday because my old one broke.", "timestamp": "2024-05-16T10:00"},
    "candidates": [{"id": "m1", "content": "Mira: My old bicycle broke.", "timestamp": "2024-05-14T10:00"}],
}
questions = {
    "semantic": Noul(instructions="Compare `new_memory.content` with `candidates[0].content`. Would a semantic link between these observations help retrieve a shared specific topic or fact?"),
    "caused_by": Noul(instructions="Compare `new_memory.content` with `candidates[0].content`. Does the candidate event cause, enable or explain the event in new_memory.content?"),
    "causes": Noul(instructions="Compare `new_memory.content` with `candidates[0].content`. Does the event in new_memory.content cause, enable or explain the candidate event?"),
}
t = time.time()
r = client.system_one(state=state, questions=questions)
print(f"{time.time()-t:.2f}s")
print(r)
