import httpx
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uuid as uuid_lib
import json
from app.config.settings import settings


class XUIClient:
   def __init__(self):
       self.base_url = settings.XUI_URL.rstrip("/")
       self.username = settings.XUI_USERNAME
       self.password = settings.XUI_PASSWORD
       self.inbound_id = settings.XUI_INBOUND_ID
       self.session_cookie: Optional[str] = None

   async def _get_headers(self) -> Dict[str, str]:
       """Get headers with session cookie"""
       headers = {
           "Accept": "application/json",
       }
       if self.session_cookie:
           headers["Cookie"] = self.session_cookie
       return headers

   async def login(self) -> bool:
       """Login to 3x-ui panel and get session cookie"""
       try:
           async with httpx.AsyncClient(verify=False) as client:
               response = await client.post(
                   f"{self.base_url}/login",
                   data={
                       "username": self.username,
                       "password": self.password,
                   },
                   headers={"Content-Type": "application/x-www-form-urlencoded"}
               )
               
               if response.status_code == 200:
                   data = response.json()
                   if data.get("success"):
                       cookies = response.cookies
                       if cookies:
                           cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                          self.session_cookie = cookie_str
                      return True
          return False
      except Exception as e:
          print(f"Login error: {e}")
          import traceback
          traceback.print_exc()
          return False

   async def _ensure_logged_in(self):
       """Ensure we have valid session"""
       if not self.session_cookie:
           await self.login()

   async def add_client(
       self,
       email: str,
       traffic_gb: int,
       expire_days: int,
       client_uuid: Optional[str] = None
   ) -> Optional[Dict[str, Any]]:
       """
       Add new client to inbound
       API endpoint: POST /panel/api/inbounds/addClient
       """
       await self._ensure_logged_in()
       
       # Calculate expiry timestamp (milliseconds)
       expiry_time = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp() * 1000)
       
       # Convert GB to bytes
       total_gb = traffic_gb * 1024 * 1024 * 1024
       
       # Generate UUID if not provided
       if not client_uuid:
           client_uuid = str(uuid_lib.uuid4())
       
       # Prepare client settings based on 3x-ui API
       client_settings = {
           "clients": [
               {
                   "id": client_uuid,
                   "alterId": 0,
                   "email": email,
                   "limitIp": 0,
                   "totalGB": total_gb,
                   "expiryTime": expiry_time,
                   "enable": True,
                   "tgId": "",
                   "subId": ""
               }
           ]
       }
       
       payload = {
           "id": self.inbound_id,
           "settings": json.dumps(client_settings)
       }
      
      try:
          async with httpx.AsyncClient(verify=False) as client:
              print(f"Sending add client request to: {self.base_url}/panel/api/inbounds/addClient")
              print(f"Payload: {payload}")
              print(f"Headers: {await self._get_headers()}")
              
              response = await client.post(
                  f"{self.base_url}/panel/api/inbounds/addClient",
                  data=payload,
                  headers=await self._get_headers()
              )
              
              print(f"Response status: {response.status_code}")
              print(f"Response body: {response.text}")
              
              if response.status_code == 200:
                  data = response.json()
                  if data.get("success"):
                      return {
                          "email": email,
                          "uuid": client_uuid,
                          "expiry_time": expiry_time,
                          "traffic_gb": traffic_gb
                      }
              print(f"Add client failed: {response.text}")
          return None
      except Exception as e:
          print(f"Add client error: {e}")
          import traceback
          traceback.print_exc()
          return None

   async def get_client_subscription(self, client_uuid: str) -> Optional[str]:
       """
       Get client subscription path
       Subscription URL format: {XUI_URL}/sub/{client_uuid}/
       """
       return f"/sub/{client_uuid}/"

   async def get_client_traffic(self, email: str) -> Optional[Dict[str, Any]]:
       """
       Get client traffic statistics
       API endpoint: GET /panel/api/inbounds/getClientTraffics/{email}
       """
       await self._ensure_logged_in()
       
       try:
           async with httpx.AsyncClient(verify=False) as client:
               response = await client.get(
                   f"{self.base_url}/panel/api/inbounds/getClientTraffics/{email}",
                   headers=await self._get_headers()
               )
               
               if response.status_code == 200:
                   data = response.json()
                   if data.get("success"):
                       return data.get("obj")
           return None
       except Exception as e:
           print(f"Get client traffic error: {e}")
           return None

   async def update_client(
       self,
       client_uuid: str,
       email: str,
       enable: bool = True,
       traffic_gb: Optional[int] = None,
       expire_days: Optional[int] = None
   ) -> bool:
       """
       Update client settings
       API endpoint: POST /panel/api/inbounds/updateClient/{client_uuid}
       """
       await self._ensure_logged_in()
       
       update_data = {
           "id": client_uuid,
           "email": email,
           "enable": enable
       }
       
       if traffic_gb is not None:
           update_data["totalGB"] = traffic_gb * 1024 * 1024 * 1024
       
       if expire_days is not None:
           update_data["expiryTime"] = int((datetime.utcnow() + timedelta(days=expire_days)).timestamp() * 1000)
       
       try:
           async with httpx.AsyncClient(verify=False) as client:
               response = await client.post(
                   f"{self.base_url}/panel/api/inbounds/updateClient/{client_uuid}",
                   data=update_data,
                   headers=await self._get_headers()
               )
               
               if response.status_code == 200:
                   data = response.json()
                   return data.get("success", False)
           return False
       except Exception as e:
           print(f"Update client error: {e}")
           return False

   async def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
       """
       Delete a client
       API endpoint: POST /panel/api/inbounds/{inbound_id}/delClient/{client_uuid}
       """
       await self._ensure_logged_in()
       
       try:
           async with httpx.AsyncClient(verify=False) as client:
               response = await client.post(
                   f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}",
                   headers=await self._get_headers()
               )
               
               if response.status_code == 200:
                   data = response.json()
                   return data.get("success", False)
           return False
       except Exception as e:
           print(f"Delete client error: {e}")
           return False

   async def reset_client_traffic(self, inbound_id: int, email: str) -> bool:
       """
       Reset client traffic statistics
       API endpoint: POST /panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}
       """
       await self._ensure_logged_in()
       
       try:
           async with httpx.AsyncClient(verify=False) as client:
               response = await client.post(
                   f"{self.base_url}/panel/api/inbounds/{inbound_id}/resetClientTraffic/{email}",
                   headers=await self._get_headers()
               )
               
               if response.status_code == 200:
                   data = response.json()
                   return data.get("success", False)
           return False
       except Exception as e:
           print(f"Reset client traffic error: {e}")
           return False


xui_client = XUIClient()
