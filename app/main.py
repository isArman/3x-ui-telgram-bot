 import asyncio
 import os
 from dotenv import load_dotenv
 
 # Load environment variables
 load_dotenv()
 
 # Ensure data directory exists
 os.makedirs("data", exist_ok=True)
 
 from app.core.runner import main
 
 if __name__ == "__main__":
     asyncio.run(main())
