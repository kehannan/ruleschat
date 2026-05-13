"""
One-shot test: re-run the original vision-failure case with the new terrain
legend wired in, and print the model's full response.

Original failure (chat_messages id=93, 2026-05-02): gpt-5.4 misread the gray
multi-hex polygon in P9/Q9 as a wreck, missed orchards on the LOS path between
the 3-3-7 in U10 and the 4-4-7 SS in Q9.

Pass criterion (the part the addendum forces the model to produce up front):
- The board-state description names the P9/Q9 feature as a Building (Stone).
- The board-state description lists the orchards on the U10 -> Q9 LOS path.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set")

from app.services.asl_service import ASLService

QUESTION = "if the 3-3-7 attacks the 4-4-7 what is the final FP and DRM on IFT?"
IMAGE = "28/08085c78cee04f50bcbcd06277fd53e9.jpg"

svc = ASLService()
print(f"=== question: {QUESTION}")
print(f"=== image:    {IMAGE}")
print(f"=== model:    gpt-5.4")
print("=== streaming response:\n")

stream, timing = svc.get_answer(
    QUESTION,
    stream=True,
    return_timing=True,
    model="gpt-5.4",
    image_path=IMAGE,
)

full = []
for delta in stream:
    sys.stdout.write(delta)
    sys.stdout.flush()
    full.append(delta)

text = "".join(full).lower()
print("\n\n=== quick checks ===")
print(f"  mentions 'building':  {'building' in text}")
print(f"  mentions 'wreck':     {'wreck' in text}")
print(f"  mentions 'orchard':   {'orchard' in text}")
print(f"  mentions 'stone':     {'stone' in text}")
print(f"\n=== timing/usage ===")
for k in ("ttft_ms", "file_search_ms", "total_ms", "input_tokens", "output_tokens"):
    if k in timing:
        print(f"  {k}: {timing[k]}")
print(f"  rag_sources: {len(timing.get('rag_sources', []))}")
