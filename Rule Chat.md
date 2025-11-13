# Rules Chat for Advanced Squad Leader (ASL)
This is an experimental site to explore AI's ability to answer rules questions about the wargame Advanced Squad Leader.

## Advanced Squad Leader (ASL)
Advanced Squad Leader (ASL) is a tactical-level hex-and-counter board game covering WW2, published by Avalon Hill in 1985. See Wikipedia [entry](https://en.wikipedia.org/wiki/Advanced_Squad_Leader).

ASL provides a system that allows simulation of any squad-level battle from WW2 and the Korean War. The core rule book contains over 1 million tokens (in comparison, Deep Learning by Goodfellow et al. is approximately 400k–420k tokens).

ASL follows an "I go you go" model, where each Game Turn consists of two Player Turns, each made up of eight Phases. The player capable of movement in his Player Turn is termed the ATTACKER; his opponent is the DEFENDER. At the end of the eight Phases, the roles are reversed and the process repeats.

During the turn Phases, the players are moving, attacking and defending with their units - infantry, armored fighting vehicles, guns -- that are represented by counters on a board made-up of hexagons.

## Rule Chat Approach
Rule Chat uses Retrieval-Augmented Generation (RAG) to answer questions about the ASL rulebook using the OpenAI Responses API and a vector store of the rulebook, which is a multimodal document that is converted into text (losing the multimodal context). 

### Evaluation
We use evals and an zero-shot AI Judge to evaluate the performance of the Rule Chat. The evals consist of a prompt and expected answer. The AI Judge then evaluates the answer against the expected answer.
