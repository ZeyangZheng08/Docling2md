TEXT_TYPE_PROMPT = """
Below is a piece of text extracted from a document. Determine whether it is a "Heading" or a "Paragraph".
Please return only 'Heading' or 'Paragraph'.

Decision rules:
1. If the text is a paragraph, a list item (e.g., starting with '·', '-', '•'), or a declarative sentence, classify it as 'Paragraph'.
2. Classify as 'Heading' if the text has the following characteristics:
   - Short length, typically no more than 15 words;
   - Does not end with a period, comma, or colon;
   - Lacks a complete subject-verb structure;
   - Usually does not start with a bullet symbol or numeric prefix (e.g., '·', '1.').

Examples:
- "Features" → Heading
- "·Low capacitance designs" → Paragraph
- "Figure 2. Output Power vs Input Voltage" → Heading
- "The device supports high reliability in harsh conditions." → Paragraph

Decide for the following text:

Text:
"""
