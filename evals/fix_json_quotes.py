import json

# Load your JSON file
with open("evals/asl_eval_results.json", "r") as file:
    data = file.read()

# Escape unescaped double quotes within strings
data = data.replace('\"', '"')  # Normalize existing escaped quotes
data = data.replace('"', '\"')  # Escape all quotes
data = data.replace('\"{', '{').replace('}\"', '}')  # Fix JSON structure quotes
data = data.replace('\"[', '[').replace(']\"', ']')  # Fix JSON structure quotes
data = data.replace('\": \"', '": "')  # Fix key-value quotes
data = data.replace('\",\"', '","')  # Fix comma-separated quotes
data = data.replace('\": ', '": ')  # Fix key-value pairs

# Save the corrected JSON back to file
with open("evals/asl_eval_results_fixed.json", "w") as file:
    file.write(data)

print("Quotes have been escaped and saved to evals/asl_eval_results_fixed.json") 