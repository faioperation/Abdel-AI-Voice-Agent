# Address Verification System Architecture

This document serves as an implementation reference for the Address Verification and Latency Optimization strategies used in the AI Voice Agent project. It can be shared as a blueprint for implementing similar robust, low-latency, speech-to-text (STT) resilient address matching in other projects.

## 1. Core Architecture Overview

The address verification module is designed to solve common voice agent problems:
- **Speech-to-Text (STT) Inaccuracies:** STT engines often concatenate words, misspell street names, or transcribe numbers as words (e.g., "Statenevej46" instead of "Statenevej 46", or "two" instead of "2").
- **Latency constraints:** Voice agents must respond instantly. Waiting for external API calls during the conversation loop introduces unacceptable delays.
- **Strict validation:** Delivery businesses need to ensure the address is 100% valid within their service zones before confirming an order.

### High-Level Flow
1. **Pre-loaded Validations:** All valid postal codes and delivery zones are loaded into memory at startup (O(1) lookup).
2. **On-Demand Street Caching:** Street names for a given postal code are fetched once from the official government API (DAWA) and cached using an LRU cache.
3. **Fuzzy & Prefix Matching:** When the agent calls the `verify_delivery_address` tool, the system normalizes the input, extracts potential house numbers (including word-based numbers), and performs a fuzzy search against the cached streets.
4. **Agent Correction:** The exact, correctly spelled address from the database is returned to the agent, and system prompts enforce that the agent uses *this exact casing and spelling* going forward.

---

## 2. Address Verification Implementation Details

### A. Street Extraction & Number Handling
STT engines often struggle with trailing numbers. The system uses a dedicated function to separate the street name from the house number, even when they are glued together or transcribed as words.

- **Trailing Word Numbers:** We maintain a vocabulary of Danish and English number words (`["en", "et", "to", "tre", ... "one", "two"]`). If a street string ends with one of these, it's peeled off and treated as the house number.
- **Digit Extraction:** Regex is used to find trailing digits.
- **Normalization:** Everything is lowercased and stripped of spaces/punctuation for comparison.

### B. Fuzzy Matching Algorithm
We use `difflib.SequenceMatcher` to find the closest matching street name. However, simple fuzzy matching isn't enough because STT might append garbage to the end of a word.

- **Prefix Bonus:** If the user's input string starts exactly like a valid street name in the database (e.g., Input = `Statenevej46`, DB = `statenevej`), we artificially boost the match ratio.
- **Bonus Calculation:** `bonus = len(db_street) / (len(input_street) + 1)`
- **Thresholds:** A match is considered successful if `ratio + bonus >= 0.95`. This aggressively corrects STT concatenations without false positives.

### C. Data Caching
Instead of querying the DAWA API for every verification:
- We use Python's `@lru_cache` (or equivalent caching mechanism) for the `_load_streets_for_postal_code` function.
- The first time a user provides a postal code, we fetch all valid streets in that code. Subsequent checks in the same postal code take `< 1ms`.

---

## 3. Latency Optimization (Voice Streaming)

Latency is critical. We optimize Time To First Token (TTFT) and audio smoothness in the streaming response loop (e.g., in `custom_llm.py`).

### Dynamic Flush Mechanism
Voice agents usually chunk text into sentences before sending them to the Text-to-Speech (TTS) engine. However, waiting for a full sentence is too slow.

- **Length-Based Flush:** We stream the LLM tokens. If a token buffer exceeds a specific character limit (e.g., **15 characters**), we immediately flush it to the TTS engine.
- **Benefit:** Unusually long words, complex Danish street names, or pauses don't stall the audio generation. The user hears the voice start speaking instantly.
- **Punctuation Boundaries:** We still flush early on natural boundaries (commas, periods) to ensure intonation is correct.

---

## 4. Best Configurations & System Prompts

To make the system work harmoniously with the LLM, the system prompt must strictly enforce the verification flow.

### System Prompt Rules (Example snippet)
We enforce a strict rule (e.g., REGEL 15) instructing the LLM:
> "If the user provides an address, you MUST call `verify_delivery_address`. If the tool returns a `suggestion` (e.g., correct spelling or casing), you MUST use that exact string in the `save_order` tool. Do not modify the casing or spelling."

### Recommended Configuration Variables
- **Fuzzy Match Minimum Ratio:** `0.75` (Standard threshold before bonus)
- **Prefix Match Threshold (Ratio + Bonus):** `0.95`
- **TTS Chunk Flush Limit:** `15` characters.
- **LRU Cache Size:** `1024` (Sufficient for thousands of concurrent sessions across different postal codes).

---

## 5. Summary for Porting to Another Project
If you are implementing this in a new project, follow these steps:
1. **Pre-cache or Lazy-cache valid options:** Never hit external APIs in the critical path of the conversation if you can avoid it.
2. **Handle STT glue:** Build logic to separate trailing numbers/words from the core entity (like a street name).
3. **Use Prefix-Boosted Fuzzy Matching:** Standard Levenshtein/SequenceMatcher will fail on STT inputs like "MainStreet4". Boost the score if `MainStreet` is a prefix.
4. **Stream with Length Thresholds:** Do not wait for punctuation to trigger TTS if the chunk gets too long. Flush at a max character limit (15-20 chars).
5. **Enforce LLM Compliance:** Explicitly tell the LLM in the system prompt to inherit the exact formatting outputted by your verification tools.
