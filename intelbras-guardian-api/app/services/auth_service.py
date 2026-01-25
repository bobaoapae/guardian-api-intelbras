"""Authentication service for Intelbras Guardian API.

Based on APK analysis, the authentication flow is:
1. OAuth 2.0 Authorization Code with PKCE (browser-based login)
2. Exchange authorization code for tokens
3. Call /api/v2/auth/mobile/login/ with device info

OAuth Configuration (from SessionHelper.java):
- Authorize URL: https://api.conta.intelbras.com/auth/authorize
- Token URL: https://api.conta.intelbras.com/auth/token
- Client ID: xHCEFEMoQnBcIHcw8ACqbU9aZaYa
- Redirect URI: We use a custom one for the middleware
- Scope: openid
- Authorization header: Token directly (not "Bearer {token}")

Authentication Flow:
1. POST /api/v1/auth/start -> Returns OAuth URL to open in browser
2. User opens URL, logs in, gets redirected with ?code=xxx
3. POST /api/v1/auth/callback with code -> Returns session_id
"""
import logging
import secrets
import uuid
import platform
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode, urlparse, parse_qs
import aiohttp

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    TokenExpiredError,
    TokenRefreshError,
    InvalidSessionError
)
from app.services.state_manager import state_manager

logger = logging.getLogger(__name__)

# PKCE pending authentications storage (state -> (code_verifier, redirect_uri))
_pending_auth: Dict[str, Tuple[str, str]] = {}


class AuthService:
    """
    Authentication service for Intelbras Guardian API.

    Handles:
    - OAuth 2.0 authentication with WSO2 Identity Server (PKCE flow)
    - Mobile login registration with Guardian API
    - Token storage and retrieval
    - Automatic token refresh
    - Session management

    Two authentication methods:
    1. OAuth PKCE (recommended): start_oauth() -> exchange_code()
    2. Password grant (if supported): authenticate()
    """

    # OAuth URLs from APK SessionHelper.java
    OAUTH_AUTHORIZE_URL = "https://api.conta.intelbras.com/auth/authorize"
    OAUTH_TOKEN_URL = "https://api.conta.intelbras.com/auth/token"
    OAUTH_LOGOUT_URL = "https://api.conta.intelbras.com/auth/logout"

    def __init__(self):
        """Initialize the auth service."""
        self.api_url = settings.INTELBRAS_API_URL
        self.oauth_url = settings.INTELBRAS_OAUTH_URL
        self.client_id = settings.INTELBRAS_CLIENT_ID
        self.refresh_buffer = settings.TOKEN_REFRESH_BUFFER
        self._session: Optional[aiohttp.ClientSession] = None
        # Generate a persistent device ID for this middleware instance
        self._device_id = str(uuid.uuid4())

    @staticmethod
    def _generate_code_verifier() -> str:
        """Generate a PKCE code verifier (43-128 chars, URL-safe)."""
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def _generate_code_challenge(code_verifier: str) -> str:
        """Generate a PKCE code challenge from verifier (S256)."""
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()

    def start_oauth(self, redirect_uri: Optional[str] = None) -> Dict[str, str]:
        """
        Start OAuth 2.0 Authorization Code flow with PKCE.

        This returns a URL that the user should open in their browser.
        After logging in, they will be redirected to a URL with ?code=xxx
        which should be passed to exchange_code().

        Args:
            redirect_uri: Custom redirect URI. If None, uses a placeholder
                         that the user will need to extract the code from.

        Returns:
            Dict with:
            - auth_url: URL to open in browser
            - state: State parameter to verify callback
            - instructions: Human-readable instructions
        """
        # Generate PKCE parameters
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = secrets.token_urlsafe(32)

        # Use a placeholder redirect URI if not provided
        # The user will see the redirect and copy the code from URL
        if not redirect_uri:
            redirect_uri = "http://localhost:8000/api/v1/auth/oauth-callback"

        # Store for later verification (code_verifier AND redirect_uri)
        _pending_auth[state] = (code_verifier, redirect_uri)

        # Build OAuth authorization URL
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }

        auth_url = f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

        logger.info(f"OAuth flow started, state: {state[:8]}...")

        return {
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": redirect_uri,
            "instructions": (
                "1. Abra a URL auth_url no navegador\n"
                "2. Faça login com sua conta Intelbras\n"
                "3. Após o login, você será redirecionado para uma URL com ?code=xxx\n"
                "4. Copie o valor do parâmetro 'code' da URL\n"
                "5. Envie o code para POST /api/v1/auth/callback"
            )
        }

    async def exchange_code(
        self,
        code: str,
        state: str,
        redirect_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exchange OAuth authorization code for tokens.

        This is step 2 of the OAuth flow. After the user logs in and
        gets redirected, they extract the 'code' parameter and call this.

        Args:
            code: Authorization code from callback URL
            state: State parameter to verify (from start_oauth response)
            redirect_uri: Optional override. If not provided, uses the one from start_oauth.

        Returns:
            Session info with session_id and expiration

        Raises:
            AuthenticationError: If code exchange fails
        """
        # Verify state and get code_verifier + stored redirect_uri
        if state not in _pending_auth:
            raise AuthenticationError(
                "Invalid or expired state. Please start a new OAuth flow.",
                {"error": "invalid_state"}
            )

        code_verifier, stored_redirect_uri = _pending_auth.pop(state)

        # Use stored redirect_uri if not explicitly provided
        if not redirect_uri:
            redirect_uri = stored_redirect_uri

        logger.debug(f"Token exchange with redirect_uri: {redirect_uri}")

        session = await self._get_session()

        # Exchange code for tokens
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }

        logger.info("Exchanging authorization code for tokens...")

        try:
            async with session.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers=headers
            ) as response:
                response_text = await response.text()
                logger.debug(f"Token exchange response {response.status}: {response_text[:300]}")

                if response.status != 200:
                    raise AuthenticationError(
                        f"Token exchange failed: {response.status}",
                        {"status": response.status, "response": response_text[:200]}
                    )

                token_data = await response.json()

                if not token_data.get("access_token"):
                    raise AuthenticationError(
                        "No access token in response",
                        {"response": response_text[:200]}
                    )

                # Register mobile device
                await self._register_mobile_device(session, token_data.get("access_token"))

                # Process and store tokens
                return await self._process_auth_response(token_data, "oauth_user")

        except aiohttp.ClientError as e:
            raise AuthenticationError(f"Token exchange failed: {e}")

    def parse_callback_url(self, url: str) -> Dict[str, str]:
        """
        Helper to parse the callback URL and extract code/state.

        The user can paste the full callback URL and this will extract
        the necessary parameters.

        Args:
            url: Full callback URL (e.g., http://localhost:8000/callback?code=xxx&state=yyy)

        Returns:
            Dict with 'code' and 'state' parameters
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if not code:
            raise AuthenticationError(
                "No 'code' parameter found in URL",
                {"url": url[:100]}
            )

        if not state:
            raise AuthenticationError(
                "No 'state' parameter found in URL",
                {"url": url[:100]}
            )

        return {"code": code, "state": state}

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=settings.HTTP_TIMEOUT)
            # Skip SSL verification for testing (Intelbras uses self-signed certs sometimes)
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _generate_session_id(self) -> str:
        """Generate a secure random session ID."""
        return secrets.token_urlsafe(32)

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user with Intelbras Guardian API.

        Authentication flow (based on APK analysis):
        1. Try OAuth 2.0 password grant to get access_token
        2. If successful, register mobile device with Guardian API
        3. Store tokens and return session

        Args:
            username: User email
            password: User password

        Returns:
            Session info with session_id and expiration

        Raises:
            AuthenticationError: If authentication fails
        """
        logger.info(f"Authenticating user: {username}")

        session = await self._get_session()

        # Step 1: Try OAuth 2.0 password grant
        token_data = await self._try_oauth_password_grant(session, username, password)

        if token_data:
            # Step 2: Register mobile device with Guardian API
            await self._register_mobile_device(session, token_data.get("access_token"))
            return await self._process_auth_response(token_data, username)

        # OAuth password grant failed, try direct endpoints
        return await self._try_direct_endpoints(session, username, password)

    async def _try_oauth_password_grant(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str
    ) -> Optional[Dict[str, Any]]:
        """
        Try OAuth 2.0 password grant authentication.

        Based on APK analysis:
        - Token URL: https://api.conta.intelbras.com/auth/token
        - Client ID: xHCEFEMoQnBcIHcw8ACqbU9aZaYa
        """
        oauth_endpoints = [
            # Primary OAuth endpoint (from APK SessionHelper)
            f"{self.oauth_url}/token",
            # Alternative paths
            f"{self.oauth_url}/oauth2/token",
            "https://api.conta.intelbras.com/auth/token",
        ]

        for endpoint in oauth_endpoints:
            try:
                logger.debug(f"Trying OAuth endpoint: {endpoint}")

                data = {
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                    "client_id": self.client_id,
                    "scope": "openid"
                }

                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "User-Agent": "IntelbrasGuardian/1.0 Android"
                }

                async with session.post(endpoint, data=data, headers=headers) as response:
                    response_text = await response.text()
                    logger.debug(f"OAuth response {response.status}: {response_text[:300]}")

                    if response.status == 200:
                        token_data = await response.json()
                        if token_data.get("access_token"):
                            logger.info("OAuth password grant successful")
                            return token_data
                    elif response.status == 400:
                        # Check if it's an unsupported grant type error
                        try:
                            error_data = await response.json()
                            error = error_data.get("error", "")
                            if "unsupported_grant_type" in error or "invalid_grant" in error:
                                logger.warning(f"OAuth password grant not supported: {error}")
                                continue
                        except:
                            pass

            except aiohttp.ClientError as e:
                logger.debug(f"OAuth connection error for {endpoint}: {e}")
            except Exception as e:
                logger.debug(f"OAuth error for {endpoint}: {e}")

        logger.warning("OAuth password grant failed on all endpoints")
        return None

    async def _register_mobile_device(
        self,
        session: aiohttp.ClientSession,
        access_token: str
    ) -> bool:
        """
        Register mobile device with Guardian API.

        Based on APK analysis (AuthenticationApiRepository.java):
        - POST /api/v2/auth/mobile/login/
        - Body: {firebase_id, mobile_id, mobile_model, mobile_name}
        - Authorization header: access_token directly (not "Bearer {token}")
        """
        try:
            url = f"{self.api_url}/api/v2/auth/mobile/login/"

            # LoginRequest fields from APK
            data = {
                "firebase_id": "",  # Optional FCM token
                "mobile_id": self._device_id,
                "mobile_model": "FastAPI-Middleware",
                "mobile_name": platform.node() or "HomeAssistant"
            }

            # Important: Authorization header uses token directly, not "Bearer {token}"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": access_token,
                "User-Agent": "IntelbrasGuardian/1.0 Android"
            }

            logger.debug(f"Registering mobile device at {url}")

            async with session.post(url, json=data, headers=headers) as response:
                response_text = await response.text()
                logger.debug(f"Mobile registration response {response.status}: {response_text[:200]}")

                if response.status in (200, 201, 204):
                    logger.info("Mobile device registered successfully")
                    return True
                else:
                    # Non-fatal - device may already be registered
                    logger.warning(f"Mobile registration returned {response.status}, continuing anyway")
                    return True

        except Exception as e:
            logger.warning(f"Mobile registration failed: {e}, continuing anyway")
            return True

    async def _try_direct_endpoints(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Try direct authentication endpoints as fallback.
        """
        auth_endpoints = [
            # Guardian API endpoints with email/password
            {
                "url": f"{self.api_url}/api/v2/auth/mobile/login/",
                "method": "json",
                "data": {"email": username, "password": password}
            },
            {
                "url": f"{self.api_url}/api/v2/auth/login/",
                "method": "json",
                "data": {"email": username, "password": password}
            },
            {
                "url": f"{self.api_url}/api/v1/auth/login/",
                "method": "json",
                "data": {"email": username, "password": password}
            },
        ]

        last_error = None
        last_status = None
        last_response = None

        for endpoint in auth_endpoints:
            try:
                logger.debug(f"Trying direct endpoint: {endpoint['url']}")

                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "IntelbrasGuardian/1.0 Android"
                }

                async with session.post(
                    endpoint["url"],
                    json=endpoint["data"],
                    headers=headers
                ) as response:
                    last_status = response.status
                    last_response = await response.text()
                    logger.debug(f"Response {response.status}: {last_response[:200]}")

                    if response.status in (200, 201):
                        token_data = await response.json()
                        return await self._process_auth_response(token_data, username)

            except aiohttp.ClientError as e:
                last_error = str(e)
                logger.debug(f"Connection error for {endpoint['url']}: {e}")
            except Exception as e:
                last_error = str(e)
                logger.debug(f"Error for {endpoint['url']}: {e}")

        # All endpoints failed
        error_msg = f"{last_status} - {last_response[:200] if last_response else last_error}"
        logger.error(f"All auth endpoints failed. Last error: {error_msg}")
        raise AuthenticationError(
            f"Authentication failed: {error_msg}",
            {"status": last_status, "response": last_response[:200] if last_response else None}
        )

    async def _process_auth_response(
        self,
        token_data: Dict[str, Any],
        username: str
    ) -> Dict[str, Any]:
        """Process successful authentication response."""
        # Extract token - different APIs return different field names
        access_token = (
            token_data.get("access_token") or
            token_data.get("token") or
            token_data.get("accessToken") or
            token_data.get("auth_token")
        )

        refresh_token = (
            token_data.get("refresh_token") or
            token_data.get("refreshToken")
        )

        expires_in = (
            token_data.get("expires_in") or
            token_data.get("expiresIn") or
            3600  # Default 1 hour
        )

        if not access_token:
            # Maybe the whole response is the token or contains user data
            # Some APIs return user data with embedded token
            if "user" in token_data or "id" in token_data:
                # API returned user data, generate a pseudo-token from session
                access_token = secrets.token_urlsafe(64)
                logger.info("API returned user data instead of token, using session-based auth")

        if not access_token:
            logger.error(f"No access token in response: {token_data}")
            raise AuthenticationError("No access token in response")

        # Calculate expiration time
        if isinstance(expires_in, str):
            expires_in = int(expires_in)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        # Generate session ID
        session_id = self._generate_session_id()

        # Store token data in state manager
        await state_manager.set_token(
            session_id=session_id,
            token_data={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at.isoformat(),
                "expires_in": expires_in,
                "username": username,
                "raw_response": token_data  # Store raw response for debugging
            }
        )

        logger.info(f"Authentication successful for {username}, session: {session_id[:8]}...")

        return {
            "session_id": session_id,
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }

    async def get_valid_token(self, session_id: str) -> str:
        """
        Get a valid access token for a session.

        Automatically refreshes the token if it's about to expire.

        Args:
            session_id: Session ID from authentication

        Returns:
            Valid access token

        Raises:
            InvalidSessionError: If session is invalid or expired
            TokenRefreshError: If token refresh fails
        """
        token_data = await state_manager.get_token(session_id)

        if not token_data:
            raise InvalidSessionError("Session not found or expired")

        expires_at_str = token_data.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)

            # Check if token needs refresh (buffer time before expiration)
            refresh_threshold = datetime.utcnow() + timedelta(seconds=self.refresh_buffer)

            if expires_at <= refresh_threshold:
                logger.info(f"Token expiring soon, attempting refresh for session {session_id[:8]}...")
                try:
                    token_data = await self._refresh_token(session_id, token_data)
                except TokenRefreshError:
                    # If refresh fails, token may still be valid
                    if expires_at <= datetime.utcnow():
                        raise TokenExpiredError("Token expired and refresh failed")

        return token_data["access_token"]

    async def _refresh_token(self, session_id: str, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Refresh an access token using the refresh token.

        Based on APK analysis (AuthServiceClient.java):
        - POST /token (OAuth endpoint)
        - Body: grant_type=refresh_token, refresh_token, client_id

        Args:
            session_id: Session ID
            token_data: Current token data with refresh_token

        Returns:
            Updated token data

        Raises:
            TokenRefreshError: If refresh fails
        """
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise TokenRefreshError("No refresh token available")

        session = await self._get_session()

        # OAuth token refresh endpoints (from APK AuthServiceClient)
        refresh_endpoints = [
            f"{self.oauth_url}/token",
            "https://api.conta.intelbras.com/auth/token",
            f"{self.oauth_url}/oauth2/token",
        ]

        for endpoint in refresh_endpoints:
            try:
                data = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id
                }
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json"
                }

                logger.debug(f"Attempting token refresh at {endpoint}")

                async with session.post(endpoint, data=data, headers=headers) as response:
                    response_text = await response.text()
                    logger.debug(f"Refresh response {response.status}: {response_text[:200]}")

                    if response.status == 200:
                        new_token_data = await response.json()
                        if new_token_data.get("access_token"):
                            logger.info("Token refresh successful")
                            return await self._update_token(session_id, token_data, new_token_data)

            except Exception as e:
                logger.debug(f"Refresh failed for {endpoint}: {e}")
                continue

        raise TokenRefreshError("All refresh endpoints failed")

    async def _update_token(
        self,
        session_id: str,
        old_data: Dict[str, Any],
        new_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update stored token data after refresh."""
        expires_in = new_data.get("expires_in", 3600)
        if isinstance(expires_in, str):
            expires_in = int(expires_in)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        updated_data = {
            "access_token": new_data.get("access_token") or new_data.get("token"),
            "refresh_token": new_data.get("refresh_token", old_data.get("refresh_token")),
            "expires_at": expires_at.isoformat(),
            "expires_in": expires_in,
            "username": old_data.get("username")
        }

        await state_manager.set_token(session_id, updated_data)
        logger.info(f"Token refreshed for session {session_id[:8]}...")

        return updated_data

    async def logout(self, session_id: str) -> bool:
        """
        Logout a session (invalidate token).

        Args:
            session_id: Session ID to invalidate

        Returns:
            True if logout successful
        """
        logger.info(f"Logging out session: {session_id[:8]}...")
        await state_manager.delete_token(session_id)
        return True

    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session information.

        Args:
            session_id: Session ID

        Returns:
            Session info dict or None if not found
        """
        token_data = await state_manager.get_token(session_id)
        if not token_data:
            return None

        return {
            "session_id": session_id,
            "username": token_data.get("username"),
            "expires_at": token_data.get("expires_at"),
            "is_valid": True
        }


# Global auth service instance
auth_service = AuthService()
