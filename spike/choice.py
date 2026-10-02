from dotenv import load_dotenv; load_dotenv()
from typesafe_sdk import Choice, Score, TypeSafeClient
r = TypeSafeClient().system_one(state={"o":"Prefers tabs over spaces in all Python projects."}, questions={
 "rep":Choice(instructions="Best representation?", criteria={"keep_separate":"distinct","merge":"compatible"}),
 "s":Score(instructions="How specific is `o`?", criteria=["Vague","Somewhat specific","Very specific"])})
print(r.answers)
