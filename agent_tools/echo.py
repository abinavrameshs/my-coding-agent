SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo text back",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False
        }
    }
}
async def run(input):
    return f"Echo: {input['text']}"
