import os
import re

INPUT_FILE = "evals/sources/eASLRB_v2.12-INHERIT_ZOOM_unlocked.txt"
OUTPUT_DIR = "evals/sources/rules_chunks"

# Regex to match rule numbers like A9.73 or A10.1 at the start of a line
RULE_HEADER_RE = re.compile(r"^(A\d+(?:\.\d+)*)(?:\s*-?\s*(.*))?")

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as infile:
    current_rule = None
    current_lines = []
    for line in infile:
        match = RULE_HEADER_RE.match(line.strip())
        if match:
            # Save previous rule if exists
            if current_rule and current_lines:
                out_path = os.path.join(OUTPUT_DIR, f"{current_rule}.txt")
                with open(out_path, "w", encoding="utf-8") as out:
                    out.write(f"### {current_rule} - {current_title}\n\n")
                    out.write("".join(current_lines).strip() + "\n")
            # Start new rule
            current_rule = match.group(1)
            current_title = match.group(2) or ""
            current_lines = [line]
        else:
            if current_rule:
                current_lines.append(line)
    # Save last rule
    if current_rule and current_lines:
        out_path = os.path.join(OUTPUT_DIR, f"{current_rule}.txt")
        with open(out_path, "w", encoding="utf-8") as out:
            out.write(f"### {current_rule} - {current_title}\n\n")
            out.write("".join(current_lines).strip() + "\n")

print("Done chunking rules.") 