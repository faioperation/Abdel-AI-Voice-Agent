import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
VAPI_API_KEY = os.getenv('VAPI_API_KEY')
VAPI_BASE = 'https://api.vapi.ai'

async def main():
    async with httpx.AsyncClient() as client:
        # 1. Get all valid tools
        res_tools = await client.get(f'{VAPI_BASE}/tool', headers={'Authorization': f'Bearer {VAPI_API_KEY}'})
        if res_tools.status_code != 200:
            print("Failed to get tools")
            return
        
        valid_tools = {t['id'] for t in res_tools.json()}
        print(f"Found {len(valid_tools)} valid tools in Vapi.")

        # 2. Get all assistants
        res_assistants = await client.get(f'{VAPI_BASE}/assistant', headers={'Authorization': f'Bearer {VAPI_API_KEY}'})
        if res_assistants.status_code != 200:
            print("Failed to get assistants")
            return
        
        assistants = res_assistants.json()
        fixed_count = 0
        
        for a in assistants:
            model = a.get('model', {})
            tool_ids = model.get('toolIds', [])
            
            # 3. Filter out invalid tools
            valid_tool_ids = [tid for tid in tool_ids if tid in valid_tools]
            
            if len(valid_tool_ids) != len(tool_ids):
                print(f"Cleaning assistant: {a.get('name')} (Removed {len(tool_ids) - len(valid_tool_ids)} missing tools)")
                
                # 4. Update the assistant with cleaned toolIds
                model['toolIds'] = valid_tool_ids
                
                patch_res = await client.patch(
                    f"{VAPI_BASE}/assistant/{a['id']}",
                    headers={'Authorization': f'Bearer {VAPI_API_KEY}'},
                    json={"model": model}
                )
                if patch_res.status_code in (200, 201):
                    fixed_count += 1
                else:
                    print(f"Failed to update {a.get('name')}: {patch_res.text}")

        print(f"Successfully cleaned up missing tools from {fixed_count} assistants.")

asyncio.run(main())
