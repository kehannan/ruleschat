## Idea #1

Goal:
Build a link from the chat responses to the pdf page that contains the answer.

When creating the RAG index, we extract section (e.g., A4.1) and the verbatim content.
When returning the answer, the chatbot should include a link to the pdf page that contains the answer.
Today we are retuning the section and trying to link that section to the specific page in the pdf.

Idea:
When creating the index, we should also extract the page number that the section appears on.
So the RAG will have section, page, and content.
And when returning the answer, we can show the section as a hyperlink where the link points to the page number.

Outcome:
FAILED. Was unable to get a proper mapping.

## Idea #2

Goal: 
1. Build a better vector store that has just the rules, and exclude tables, indexes, etc. which are causing low recall. 
2. Structure the vector store so that it is easy to link to the page number that the section appears on.

Idea:
Build the vector store using just section headers, and individual rules sections that are defined by a pattern like "5.11 MOVEMENT:".

Start by building an index of rules sections (e.g., ' A 3.1 RALLY PHASE (RPh):' or 'A 3 BASIC SEQUENCE OF PLAY'). The complexity is you will need to pull the section letter (e.g., 'A') from the page header which shows the section letter in big bold letters.

In the index you build, structure like this:
Section Letter: A
Section: A3 or A3.1
Section title: BASIC SEQUENCE OF PLAY
Page: 48

To simplify, just build the index for Section Letter: A.
