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


