import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
VAPI_API_KEY = os.getenv('VAPI_API_KEY')
VAPI_BASE = 'https://api.vapi.ai'

async def main():
    async with httpx.AsyncClient() as client:
        res = await client.get(f'{VAPI_BASE}/assistant', headers={'Authorization': f'Bearer {VAPI_API_KEY}'})
        if res.status_code != 200:
            print("Failed to get assistants", res.text)
            return
        
        assistants = res.json()
        count = 0
        for a in assistants:
            model = a.get('model', {})
            if model.get('provider') == 'custom-llm':
                print(f"Fixing assistant provider: {a.get('name')} ({a['id']})")
                
                # Remove url and headers, force openai
                new_model = {
                    "provider": "openai",
                    "model": model.get("model", "gpt-4o"),
                    "messages": model.get("messages", []),
                    "toolIds": model.get("toolIds", []),
                    "temperature": model.get("temperature", 0.3)
                }
                
                patch_res = await client.patch(
                    f"{VAPI_BASE}/assistant/{a['id']}",
                    headers={'Authorization': f'Bearer {VAPI_API_KEY}'},
                    json={"model": new_model}
                )
                
                if patch_res.status_code in (200, 201):
                    print("  Successfully fixed!")
                    count += 1
                else:
                    print(f"  Failed to fix: {patch_res.text}")
        print(f"Fixed {count} assistants to use OpenAI in total.")

asyncio.run(main())
