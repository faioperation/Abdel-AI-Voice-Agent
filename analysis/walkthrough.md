# Walkthrough: Latency and Address Matching Fixes

I have successfully implemented the changes outlined in the implementation plan to improve address parsing stability and reduce latency.

## What was changed

### 1. Robust Address Matching (`app/routes/address.py`)
I added a `prefix match bonus` to the fuzzy matching logic. 
If the speech-to-text engine falsely appends the house number as a word (e.g. producing `"statenevejseksogfyre"`), the fuzzy matcher will now detect that it starts perfectly with a valid street name from the database (`"statenevej"`). When this happens, it forces a high match ratio (`0.95`), ensuring that the system locks onto the correct street name instead of guessing completely different streets (like "Søndre Ringvej").

### 2. Strict Casing Enforcement (`system_prompt.txt`)
I updated `REGEL 15` in the system prompt. The prompt now explicitly commands the LLM to use the *exact* spelling provided by the `verify_delivery_address` tool (e.g., `Statenevej` instead of `Statene Vej`). This prevents the LLM from hallucinating spacing when writing the address to the database.

> [!TIP]
> You may need to run your prompt update script (e.g. `fix_all_assistants.py` or similar) if you haven't already, so that Vapi pulls in this new system prompt.

### 3. Length-Based Dynamic Flushing (`app/routes/custom_llm.py`)
I updated the stream buffering logic in the custom LLM middleware.
Previously, the buffer held all incoming text until it saw a space or punctuation. While this was safe for phonetic processing, it artificially held back long words. 
I added a length threshold: if the buffer reaches 15 characters and still hasn't found a boundary, it will dynamically flush immediately. This prevents long, un-spaced words (like long Danish street names) from holding up the TTS engine, effectively smoothing out audio delivery.

## Validation
- The prefix matching safely ignores appended number words without touching the `extract_street_name` core logic, maintaining backwards compatibility.
- The system prompt strictly warns against address format manipulation.
- The stream buffer now has a safety valve against indefinite blocking.

Everything is ready to test on a live call!
