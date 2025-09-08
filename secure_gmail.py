# filename: secure_gmail.py
"""
Secure Gmail client with token encryption and automatic refresh.
Handles OAuth2 tokens with optional encryption for enhanced security.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from simplegmail import Gmail

logger = logging.getLogger(__name__)

class SecureGmailClient:
    """Gmail client with secure token management and automatic refresh."""
    
    def __init__(self, client_secrets_file: str, token_file: str, scopes: list, 
                 encryption_key: Optional[str] = None, refresh_threshold_hours: int = 1):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.scopes = scopes
        self.encryption_key = encryption_key
        self.refresh_threshold_hours = refresh_threshold_hours
        self._gmail_client: Optional[Gmail] = None
        self._credentials: Optional[Credentials] = None
        self._cipher_suite: Optional[Fernet] = None
        
        if encryption_key:
            self._setup_encryption()
    
    def _setup_encryption(self):
        """Setup encryption for token storage."""
        try:
            # Derive key from password
            password = self.encryption_key.encode()
            salt = b'noc_buzzer_salt'  # In production, use random salt
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(password))
            self._cipher_suite = Fernet(key)
            logger.info("Token encryption initialized")
        except Exception as e:
            logger.error(f"Failed to setup encryption: {e}")
            raise
    
    def _encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        if not self._cipher_suite:
            return data
        return self._cipher_suite.encrypt(data.encode()).decode()
    
    def _decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        if not self._cipher_suite:
            return encrypted_data
        return self._cipher_suite.decrypt(encrypted_data.encode()).decode()
    
    def _save_credentials(self, credentials: Credentials):
        """Save credentials to file with optional encryption."""
        try:
            token_data = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes,
                'expiry': credentials.expiry.isoformat() if credentials.expiry else None,
                'created_at': datetime.now().isoformat(),
                'encrypted': bool(self._cipher_suite)
            }
            
            token_json = json.dumps(token_data)
            
            if self._cipher_suite:
                token_json = self._encrypt_data(token_json)
            
            with open(self.token_file, 'w') as f:
                if self._cipher_suite:
                    f.write(token_json)
                else:
                    json.dump(token_data, f, indent=2)
            
            # Set restrictive permissions
            os.chmod(self.token_file, 0o600)
            logger.info(f"Credentials saved to {self.token_file}")
            
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
            raise
    
    def _load_credentials(self) -> Optional[Credentials]:
        """Load credentials from file with optional decryption."""
        if not Path(self.token_file).exists():
            return None
        
        try:
            with open(self.token_file, 'r') as f:
                content = f.read()
            
            # Try to parse as JSON first (unencrypted)
            try:
                token_data = json.loads(content)
                if not token_data.get('encrypted', False):
                    # Unencrypted token
                    pass
                else:
                    # This shouldn't happen but handle gracefully
                    logger.warning("Token file marked as encrypted but loaded as JSON")
            except json.JSONDecodeError:
                # Assume encrypted
                if not self._cipher_suite:
                    logger.error("Token file appears encrypted but no encryption key provided")
                    return None
                
                decrypted_content = self._decrypt_data(content)
                token_data = json.loads(decrypted_content)
            
            # Parse expiry date
            expiry = None
            if token_data.get('expiry'):
                expiry = datetime.fromisoformat(token_data['expiry'])
            
            credentials = Credentials(
                token=token_data['token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri=token_data.get('token_uri'),
                client_id=token_data.get('client_id'),
                client_secret=token_data.get('client_secret'),
                scopes=token_data.get('scopes'),
                expiry=expiry
            )
            
            logger.info("Credentials loaded successfully")
            return credentials
            
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return None
    
    def _needs_refresh(self, credentials: Credentials) -> bool:
        """Check if credentials need refresh."""
        if not credentials.expiry:
            return False
        
        threshold = datetime.now() + timedelta(hours=self.refresh_threshold_hours)
        return credentials.expiry <= threshold
    
    def _refresh_credentials(self, credentials: Credentials) -> Credentials:
        """Refresh expired credentials."""
        try:
            logger.info("Refreshing Gmail credentials")
            credentials.refresh(Request())
            self._save_credentials(credentials)
            logger.info("Credentials refreshed successfully")
            return credentials
        except Exception as e:
            logger.error(f"Failed to refresh credentials: {e}")
            raise
    
    def _authenticate(self) -> Credentials:
        """Authenticate and get credentials."""
        credentials = self._load_credentials()
        
        if credentials and credentials.valid:
            if self._needs_refresh(credentials):
                credentials = self._refresh_credentials(credentials)
            return credentials
        
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials = self._refresh_credentials(credentials)
                return credentials
            except Exception as e:
                logger.warning(f"Failed to refresh expired credentials: {e}")
        
        # Need new authentication
        logger.info("Starting new OAuth2 flow")
        flow = InstalledAppFlow.from_client_secrets_file(
            self.client_secrets_file, self.scopes
        )
        credentials = flow.run_local_server(port=0)
        self._save_credentials(credentials)
        
        return credentials
    
    def get_gmail_client(self) -> Gmail:
        """Get authenticated Gmail client."""
        if self._gmail_client is None:
            try:
                self._credentials = self._authenticate()
                
                # Create Gmail instance (simplegmail handles credentials internally)
                self._gmail_client = Gmail()
                
                logger.info("Gmail client initialized successfully")
                
            except Exception as e:
                logger.error(f"Failed to initialize Gmail client: {e}")
                raise
        
        return self._gmail_client
    
    def check_connection(self) -> bool:
        """Check if Gmail connection is working."""
        try:
            gmail = self.get_gmail_client()
            # Try to get labels as a health check
            gmail.get_unread_inbox()
            return True
        except Exception as e:
            logger.error(f"Gmail connection check failed: {e}")
            return False
    
    def cleanup(self):
        """Cleanup resources."""
        self._gmail_client = None
        self._credentials = None
        logger.info("Gmail client cleaned up")