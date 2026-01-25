# FastAPI Middleware - API Documentation

This document describes all REST endpoints exposed by the FastAPI middleware.

## Base URL

```
http://localhost:8000/api/v1
```

## Authentication

All endpoints except `/auth/login` and `/health` require authentication via session ID.

Include the session ID in the request header:
```
X-Session-ID: your-session-id
```

---

## Health Check

### GET /health

Check if the API is running.

**Response:**
```json
{
  "status": "healthy"
}
```

---

## Authentication Endpoints

### POST /auth/login

Authenticate with Intelbras credentials.

**Request Body:**
```json
{
  "username": "email@example.com",
  "password": "your-password"
}
```

**Response:**
```json
{
  "session_id": "uuid-session-id",
  "expires_at": "2024-01-25T12:00:00Z"
}
```

**Errors:**
- `401`: Invalid credentials

---

### POST /auth/logout

Invalidate current session.

**Headers:**
- `X-Session-ID`: Your session ID

**Response:**
```json
{
  "message": "Logged out successfully"
}
```

---

### GET /auth/session

Get current session information.

**Headers:**
- `X-Session-ID`: Your session ID

**Response:**
```json
{
  "session_id": "uuid-session-id",
  "username": "email@example.com",
  "expires_at": "2024-01-25T12:00:00Z"
}
```

---

## Device Endpoints

### GET /devices

List all alarm panels (centrais) associated with the account.

**Headers:**
- `X-Session-ID`: Your session ID

**Response:**
```json
[
  {
    "id": 12345,
    "description": "Casa",
    "mac": "AA:BB:CC:DD:EE:FF",
    "model": "AMT 2018",
    "has_saved_password": true,
    "partitions_enabled": false,
    "partitions": [
      {
        "id": 0,
        "name": "Alarme",
        "status": "disarmed",
        "is_in_alarm": false
      }
    ],
    "zones": [
      {
        "id": 1,
        "name": "Zone 01",
        "status": "INACTIVE"
      }
    ]
  }
]
```

---

### GET /devices/{device_id}

Get details of a specific device.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Response:**
Same as single device in `/devices` response.

---

## Password Management

### POST /devices/{device_id}/password

Save device password for auto-sync functionality.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Password saved successfully"
}
```

**Notes:**
- Password is stored encrypted in memory
- Required for real-time ISECNet status

---

### DELETE /devices/{device_id}/password

Delete saved device password.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Response:**
```json
{
  "success": true,
  "message": "Password deleted successfully"
}
```

---

## Alarm Control Endpoints

### POST /alarm/{device_id}/arm

Arm a partition.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "partition_id": 0,
  "mode": "away",
  "password": "device-password"
}
```

**Mode Options:**
- `away`: Arm all zones (total)
- `home`: Arm perimeter only (stay/partial)

**Response:**
```json
{
  "success": true,
  "new_status": "armed_away"
}
```

**Errors:**
- `400`: Open zones prevent arming (returns list of open zones)
- `401`: Invalid password
- `404`: Device or partition not found

---

### POST /alarm/{device_id}/disarm

Disarm a partition.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "partition_id": 0,
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "new_status": "disarmed"
}
```

---

### GET /alarm/{device_id}/status

Get real-time alarm status via ISECNet protocol.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Query Parameters:**
- `password`: Device password (required)

**Response:**
```json
{
  "arm_mode": "disarmed",
  "is_armed": false,
  "is_triggered": false,
  "partitions_enabled": false,
  "partitions": [
    {
      "index": 0,
      "state": "disarmed"
    }
  ],
  "zones": [
    {
      "index": 0,
      "is_open": false,
      "is_bypassed": false
    }
  ]
}
```

---

### GET /alarm/{device_id}/status/auto

Get real-time status using saved password.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Response:**
Same as `/alarm/{device_id}/status`

**Errors:**
- `404`: No saved password for device

---

## Zone Endpoints

### GET /devices/{device_id}/zones

Get all zones with their friendly names.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Response:**
```json
{
  "device_id": 12345,
  "zones": [
    {
      "index": 0,
      "name": "Zone 01",
      "friendly_name": "Front Door",
      "is_open": false,
      "is_bypassed": false
    },
    {
      "index": 1,
      "name": "Zone 02",
      "friendly_name": null,
      "is_open": true,
      "is_bypassed": false
    }
  ]
}
```

---

### PUT /devices/{device_id}/zones/{zone_index}/friendly-name

Set a friendly name for a zone.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)
- `zone_index`: Zone index (0-based)

**Request Body:**
```json
{
  "friendly_name": "Front Door"
}
```

**Response:**
```json
{
  "success": true,
  "zone_index": 0,
  "friendly_name": "Front Door"
}
```

---

### DELETE /devices/{device_id}/zones/{zone_index}/friendly-name

Delete the friendly name for a zone.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)
- `zone_index`: Zone index (0-based)

**Response:**
```json
{
  "success": true,
  "message": "Friendly name deleted"
}
```

---

## Event Endpoints

### GET /events

Get alarm event history.

**Headers:**
- `X-Session-ID`: Your session ID

**Query Parameters:**
- `limit`: Maximum number of events (default: 50)
- `offset`: Pagination offset (default: 0)

**Response:**
```json
[
  {
    "id": 123456,
    "timestamp": "2024-01-24T10:30:00Z",
    "event_type": "alarm_triggered",
    "device_id": 12345,
    "partition_id": 0,
    "zone": {
      "id": 1,
      "name": "Zone 01",
      "friendly_name": "Front Door"
    },
    "notification": {
      "code": 1000,
      "title": "Alarm Triggered",
      "message": "Zone 01 was triggered"
    }
  }
]
```

---

## Eletrificador (Electric Fence) Endpoints

### POST /eletrificador/{device_id}/shock/on

Enable electric shock.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Shock enabled"
}
```

---

### POST /eletrificador/{device_id}/shock/off

Disable electric shock.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Shock disabled"
}
```

---

### POST /eletrificador/{device_id}/alarm/activate

Arm the fence alarm.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Alarm activated"
}
```

---

### POST /eletrificador/{device_id}/alarm/deactivate

Disarm the fence alarm.

**Headers:**
- `X-Session-ID`: Your session ID

**Path Parameters:**
- `device_id`: Device ID (integer)

**Request Body:**
```json
{
  "password": "device-password"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Alarm deactivated"
}
```

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message description"
}
```

### Common HTTP Status Codes

| Code | Description |
|------|-------------|
| 400  | Bad Request - Invalid parameters |
| 401  | Unauthorized - Invalid session or credentials |
| 404  | Not Found - Resource doesn't exist |
| 500  | Internal Server Error |

### Open Zones Error

When arming fails due to open zones (error 0xE4):

```json
{
  "detail": "Cannot arm: zones are open",
  "error_code": "open_zones",
  "open_zones": [0, 2, 5]
}
```

---

## Interactive Documentation

When the FastAPI middleware is running, access:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
