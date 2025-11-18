TEXT_REPAIR_PROMPT = """
Please perform reasonable English word segmentation on the following continuous string so that it becomes a readable English sentence or phrase.
Notes:
- Preserve the original meaning.
- Do not add or remove any characters.
- Insert spaces only where necessary to make natural language.
- If a character's intended meaning is ambiguous, consider possible special-character substitutions (for example, a '●' might represent the letter 'O') and handle accordingly.

Example input:
THISISATESTSTRING

Example output:
THIS IS A TEST STRING

Output only the processed text; do not include any explanations or extra content.

Text to process:
"""
