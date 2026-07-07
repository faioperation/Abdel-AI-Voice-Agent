# Fix Address Extraction and Latency Issues

This plan addresses two main issues observed in recent call transcripts and latency reports:
1. **Address Matching Errors**: The agent asks for the address multiple times and saves the street name incorrectly (e.g., "Statene Vej" instead of "Statenevej").
2. **High Latency**: The LLM TTFT (Time To First Token) and turn latency have increased significantly.

## Proposed Changes

### 1. Improve Address Extraction (`app/routes/address.py`)
**Problem**: The current `extract_street_name` function assumes house numbers always start with a digit (`tokens[-1][0].isdigit()`). When the STT engine transcribes house numbers as words (e.g., "seksogfyre" or "forty-six"), the function treats the number as part of the street name. This ruins the fuzzy matching ratio, causing it to match the wrong street (e.g., "Søndre Ringvej").
**Solution**:
- Enhance `extract_street_name` to filter out common Danish/English number words at the end of the address string.
- Adjust the fuzzy matching so it relies on the prefix sequence, preventing appended words from artificially destroying a correct match.

### 2. Enforce Exact Address Casing (`system_prompt.txt`)
**Problem**: Even when the address tool returns the correct spelling ("Statenevej 46"), the LLM sometimes hallucinates and splits compound street names ("Statene Vej 46") when calling the `save_order` tool.
**Solution**:
- Update `REGEL 15` in `system_prompt.txt` to explicitly instruct the LLM: "Når du kalder `save_order`, SKAL du bruge den NØJAGTIGE stavemåde og formatering, som værktøjet `verify_delivery_address` returnerede i feltet `suggestion`."

### 3. Optimize Latency (`app/routes/custom_llm.py`)
**Current Situation (Why Latency is High)**:
Right now, you are using a Custom LLM endpoint (`app/routes/custom_llm.py`) that sits between Vapi and OpenAI. Its job is to apply phonetic pronunciation fixes to the Danish text before sending it to the Cartesia Voice TTS. 

To make sure it doesn't break words in half when applying phonetic fixes (e.g., changing "champagne" to "champignon"), your current code **buffers** the tokens streaming from OpenAI. It forcibly holds the text back and refuses to send it to Vapi until OpenAI generates a complete word ending with a space or punctuation mark (like `. , ? !`). 

Because OpenAI generates text in tiny chunks (tokens) like `["Sel", "vf", "ølg", "elig", ","]`, your server holds back the first token (`Sel`) for several hundreds of milliseconds until the comma arrives. This buffering directly inflates the Time To First Token (TTFT) and contributes to the ~3-second Turn Latency you see in the dashboard, because the Voice engine has to sit idle waiting for your server to flush the buffer.

**Proposed Improvement**:
We will optimize the streaming loop in `custom_llm.py` to reduce this artificial latency:
- **Fast-Track the First Token**: For the very first chunk of a sentence, we will bypass or loosen the strict boundary requirement so Vapi receives the first token almost instantly. This tells the Voice engine to start synthesizing audio immediately.
- **Dynamic Flushing**: If the buffer is held for too long or exceeds a certain character limit, we will force a flush rather than waiting indefinitely for a boundary character.
- **Empty Chunk Ping**: Ensure we are rapidly sending empty metadata chunks back to Vapi while buffering, to keep the connection fully alive and prevent Vapi from assuming a network stall.

## Verification Plan
### Automated Tests
- Run the local `scratch_test.py` with STT-like inputs (e.g., "Statenevej seksogfyre 46") to verify the new fuzzy matching correctly resolves to "Statenevej 46".
### Manual Verification
- Place a test call and speak the address with a word-based house number ("Statenevej seksogfyre").
- Check the Vapi dashboard to confirm the LLM latency has decreased and TTFT is faster.
- Check the backend dashboard to ensure the order is saved as "Statenevej 46" and not "Statene Vej 46".
