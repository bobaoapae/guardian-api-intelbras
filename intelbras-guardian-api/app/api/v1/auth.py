"""Authentication endpoints.

Supports two authentication methods:
1. OAuth PKCE (recommended): /auth/start -> /auth/callback
2. Password grant (fallback): /auth/login
"""
from fastapi import APIRouter, HTTPException, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.services.auth_service import auth_service
from app.core.exceptions import AuthenticationError, InvalidSessionError

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ==================== Request/Response Models ====================

class LoginRequest(BaseModel):
    """Login request model (password grant)."""
    username: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="User password")


class LoginResponse(BaseModel):
    """Login response model."""
    session_id: str = Field(..., description="Session ID for subsequent requests")
    expires_at: str = Field(..., description="Session expiration timestamp")
    message: str = Field(default="Login successful")


class OAuthStartResponse(BaseModel):
    """OAuth start response model."""
    auth_url: str = Field(..., description="URL to open in browser for login")
    state: str = Field(..., description="State parameter (needed for callback)")
    redirect_uri: str = Field(..., description="Redirect URI used")
    instructions: str = Field(..., description="Instructions for completing login")


class OAuthCallbackRequest(BaseModel):
    """OAuth callback request model."""
    code: str = Field(..., description="Authorization code from callback URL")
    state: str = Field(..., description="State parameter from start response")
    redirect_uri: Optional[str] = Field(None, description="Must match start request")


class OAuthCallbackURLRequest(BaseModel):
    """OAuth callback using full URL."""
    callback_url: str = Field(..., description="Full callback URL with code and state")
    redirect_uri: Optional[str] = Field(None, description="Must match start request")


class LogoutResponse(BaseModel):
    """Logout response model."""
    success: bool = Field(..., description="Whether logout was successful")
    message: str = Field(default="Logged out successfully")


class SessionResponse(BaseModel):
    """Session info response model."""
    session_id: str = Field(..., description="Session ID")
    username: Optional[str] = Field(None, description="Username")
    expires_at: Optional[str] = Field(None, description="Session expiration")
    is_valid: bool = Field(..., description="Whether session is valid")


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user with Intelbras credentials.

    Returns a session_id that must be passed in X-Session-ID header
    for all subsequent requests.
    """
    try:
        result = await auth_service.authenticate(request.username, request.password)
        return LoginResponse(
            session_id=result["session_id"],
            expires_at=result["expires_at"],
            message="Login successful"
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.post("/logout", response_model=LogoutResponse)
async def logout(x_session_id: str = Header(..., alias="X-Session-ID")):
    """
    Logout and invalidate session.

    Requires X-Session-ID header.
    """
    try:
        await auth_service.logout(x_session_id)
        return LogoutResponse(success=True, message="Logged out successfully")
    except Exception as e:
        return LogoutResponse(success=False, message=str(e))


@router.get("/session", response_model=SessionResponse)
async def get_session(x_session_id: str = Header(..., alias="X-Session-ID")):
    """
    Get session information.

    Requires X-Session-ID header.
    """
    try:
        info = await auth_service.get_session_info(x_session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionResponse(**info)
    except InvalidSessionError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


# ==================== OAuth PKCE Endpoints ====================

@router.post("/start", response_model=OAuthStartResponse)
async def start_oauth(request: Request, redirect_uri: Optional[str] = None):
    """
    Start OAuth 2.0 login flow (Step 1).

    Returns a URL that you should open in a browser. After logging in,
    you'll be redirected to a URL containing a 'code' parameter.

    Flow:
    1. Call this endpoint to get auth_url
    2. Open auth_url in browser
    3. Login with your Intelbras account
    4. After login, copy the 'code' from the redirect URL
    5. Call POST /auth/callback with the code and state
    """
    # Use the request's base URL if no redirect_uri provided
    if not redirect_uri:
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/api/v1/auth/oauth-callback"

    result = auth_service.start_oauth(redirect_uri)
    return OAuthStartResponse(**result)


@router.post("/callback", response_model=LoginResponse)
async def oauth_callback(request: OAuthCallbackRequest):
    """
    Complete OAuth 2.0 login (Step 2).

    After opening the auth_url and logging in, you'll be redirected
    to a URL containing 'code' and 'state' parameters. Pass them here.
    """
    try:
        result = await auth_service.exchange_code(
            code=request.code,
            state=request.state,
            redirect_uri=request.redirect_uri
        )
        return LoginResponse(
            session_id=result["session_id"],
            expires_at=result["expires_at"],
            message="OAuth login successful"
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.post("/callback-url", response_model=LoginResponse)
async def oauth_callback_url(request: OAuthCallbackURLRequest):
    """
    Complete OAuth 2.0 login using full callback URL.

    Alternative to /callback - just paste the full redirect URL
    and we'll extract the code and state for you.

    Example: If you were redirected to:
    http://localhost:8000/api/v1/auth/oauth-callback?code=xxx&state=yyy

    Just paste the whole URL here.
    """
    try:
        params = auth_service.parse_callback_url(request.callback_url)
        result = await auth_service.exchange_code(
            code=params["code"],
            state=params["state"],
            redirect_uri=request.redirect_uri
        )
        return LoginResponse(
            session_id=result["session_id"],
            expires_at=result["expires_at"],
            message="OAuth login successful"
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e.message))


@router.get("/oauth-callback", response_class=HTMLResponse)
async def oauth_callback_redirect(
    code: str = Query(..., description="Authorization code"),
    state: str = Query(..., description="State parameter")
):
    """
    OAuth callback endpoint - handles the redirect from Intelbras login.

    This is called automatically by the browser after login.
    It shows a page with the code and instructions.
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Intelbras Guardian - Login Successful</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 600px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }}
            .card {{
                background: white;
                padding: 30px;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{ color: #28a745; }}
            .code-box {{
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 15px;
                margin: 15px 0;
                word-break: break-all;
                font-family: monospace;
            }}
            .btn {{
                background: #007bff;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
            }}
            .btn:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>✓ Login Successful!</h1>
            <p>Authorization code received. You can now complete the authentication.</p>

            <h3>Option 1: Copy the code</h3>
            <div class="code-box">
                <strong>Code:</strong> {code}
            </div>
            <div class="code-box">
                <strong>State:</strong> {state}
            </div>
            <p>Use these in POST /api/v1/auth/callback</p>

            <h3>Option 2: Use the full URL</h3>
            <div class="code-box" id="fullUrl"></div>
            <button class="btn" onclick="copyUrl()">Copy URL</button>
            <p>Use this in POST /api/v1/auth/callback-url</p>

            <h3>Option 3: Complete automatically</h3>
            <p>Click the button below to complete authentication:</p>
            <button class="btn" onclick="completeAuth()">Complete Login</button>
            <div id="result" style="margin-top: 15px;"></div>
        </div>

        <script>
            document.getElementById('fullUrl').textContent = window.location.href;

            function copyUrl() {{
                navigator.clipboard.writeText(window.location.href);
                alert('URL copied to clipboard!');
            }}

            async function completeAuth() {{
                const resultDiv = document.getElementById('result');
                resultDiv.innerHTML = 'Processing...';

                try {{
                    const response = await fetch('/api/v1/auth/callback', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            code: '{code}',
                            state: '{state}'
                        }})
                    }});

                    const data = await response.json();

                    if (response.ok) {{
                        resultDiv.innerHTML = `
                            <div style="color: green;">
                                <strong>✓ Authentication Complete!</strong><br>
                                Session ID: <code>${{data.session_id}}</code><br>
                                Expires: ${{data.expires_at}}
                            </div>
                        `;
                        // Store session in localStorage for Web UI
                        localStorage.setItem('session_id', data.session_id);
                    }} else {{
                        resultDiv.innerHTML = `<div style="color: red;">Error: ${{data.detail}}</div>`;
                    }}
                }} catch (e) {{
                    resultDiv.innerHTML = `<div style="color: red;">Error: ${{e.message}}</div>`;
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
