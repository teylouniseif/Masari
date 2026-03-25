# **Channel-Based Conversation Manager**

This package provides a framework for managing AI-driven conversations using OpenAI's GPT models. It enables multi-conversation processing, with concurrency handling, with support for persistent storage and retrieval of conversation histories.

---

## **Features**

- Manage multiple conversations simultaneously using a `Channel`.
- Integrates with OpenAI's GPT-4 for natural language understanding.
- Support for storing and retrieving conversation histories.
- Queue-based architecture for processing user messages.
- Channels allow for Conversation segragating and prioritising
- Messages within each Conversation are guaranteed to be processed sequentially
- Stringified output for conversations.

---

## **Installation**

Install the package using `pip`:

```bash
pip install git+https://github.com/teylouniseif/erdos.git
```

---
## **Classes and Methods**

Channel
Manages multiple conversations.

**Initialization**

```python
channel = Channel(name: str, llm_api_key: str, max_conversations: int)
```

- **Attributes**:
  - `name`: Name of the channel.
  - `llm_api_key`: OpenAI API key.
  - `max_conversations`: Maximum number of conversations.

- **Methods**:
  - `add_conversation(conversation_id: int)`: Adds a new conversation.
  - `get_conversation(conversation_id: int)`: Retrieves a specific conversation.

---

**Conversation**

Handles individual conversations within a `Channel`.

- **Methods**:
  - `add_message(message: str)`: Sends a message to the AI.
  - `get_messages(stringified: bool = False)`: Retrieves the conversation history.
  - `store()`: Stores the conversation in persistent storage.
  - `retrieve()`: Retrieves the stored conversation history.

## **Usage**

1. Initialize a Channel and Add Conversations

```python
import os
import time
from erdos import Channel

# Initialize a Channel
channel = Channel("channel", os.getenv("OPENAI_API_KEY"))

# Add a conversation and send messages
conversation = channel.add_conversation(4)
conversation.add_message("What is 2+2?")
conversation.add_message("Add 4 to the previous result")

# Add a second conversation
second_conversation = channel.add_conversation(3)
second_conversation.add_message("What is 3*3?")
second_conversation.add_message("Add 1 to the response")

# Wait for the conversations to process
time.sleep(5)

# Retrieve and print messages
print(conversation.get_messages(stringified=True))
print(second_conversation.get_messages(stringified=True))
```

2. Store and Retrieve Conversations

```python
# Store the first conversation
conversation.store()

# Initialize a new channel
channel = Channel("channel_2", os.getenv("OPENAI_API_KEY"))

# Retrieve the stored conversation
conversation = channel.add_conversation(4)
conversation.retrieve()

# Continue the conversation
conversation.add_message("Add 7 to the previous result")

# Wait for processing
time.sleep(5)

# Print the updated conversation
print(conversation.get_messages(stringified=True))

```

## **License**

This project is licensed under the MIT License. See the LICENSE file for details.

---

## Run `test_options_buy_strategy.py`

This test calls the live Questrade API, so it needs dependencies plus a valid Questrade refresh token.

1. Install dependencies:

```bash
python3 -m pip install --user -e .
```

2. Copy the template and fill your refresh token:

```bash
cp questrade_credentials.template.json questrade_credentials.json
```

Then edit `questrade_credentials.json` and set the `refresh_token` field:

```json
{
  "refresh_token": "your_fresh_refresh_token_here"
}
```

Note: if you see `refresh_toek` anywhere, that is a typo; use `refresh_token`.

3. Run the test script:

```bash
python3 test_options_buy_strategy.py
```

Notes:
- Questrade refresh tokens are single-use/short-lived; generate a new one if you get HTTP 400.
- The script is an integration test and depends on network access to `login.questrade.com`.

## Paper Option Execution (Questrade Practice)

This repo now includes a sandbox execution module that takes opportunities from `OptionsBuyStrategy` and builds Questrade option orders:

- Module: `investment_strategies/questrade_optionsplay_sandbox.py`
- Script: `test_optionsplay_sandbox_execution.py`
- Uses:
  - `POST /accounts/:id/orders/impact` (preview)
  - `POST /accounts/:id/orders` (submit)

### Setup

1. Use a **practice** refresh token.
2. Set `token_url` in `questrade_credentials.json`:

```json
{
  "refresh_token": "your_practice_refresh_token",
  "token_url": "https://practicelogin.questrade.com/oauth2/token"
}
```

### Run

Preview-only (default):

```bash
python3 test_optionsplay_sandbox_execution.py
```

Submit to sandbox:

```bash
python3 test_optionsplay_sandbox_execution.py --submit
```

If your token/app does not have trade permission, Questrade will reject order endpoints with HTTP 403.
