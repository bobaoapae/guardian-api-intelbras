# Documentação da API FastAPI

Este documento descreve todos os endpoints REST expostos pelo middleware FastAPI.

## URL Base

```
http://localhost:8000/api/v1
```

## Autenticação

Todos os endpoints exceto `/auth/login` e `/health` requerem autenticação via session ID.

Inclua o session ID no header da requisição:
```
X-Session-ID: seu-session-id
```

---

## Health Check

### GET /health

Verifica se a API está rodando.

**Resposta:**
```json
{
  "status": "healthy"
}
```

---

## Endpoints de Autenticação

### POST /auth/login

Autentica com credenciais Intelbras.

**Body da Requisição:**
```json
{
  "username": "email@exemplo.com",
  "password": "sua-senha"
}
```

**Resposta:**
```json
{
  "session_id": "uuid-session-id",
  "expires_at": "2024-01-25T12:00:00Z"
}
```

**Erros:**
- `401`: Credenciais inválidas

---

### POST /auth/logout

Invalida a sessão atual.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "message": "Logout realizado com sucesso"
}
```

---

### GET /auth/session

Obtém informações da sessão atual.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
```json
{
  "session_id": "uuid-session-id",
  "username": "email@exemplo.com",
  "expires_at": "2024-01-25T12:00:00Z"
}
```

---

## Endpoints de Dispositivos

### GET /devices

Lista todas as centrais de alarme associadas à conta.

**Headers:**
- `X-Session-ID`: Seu session ID

**Resposta:**
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
        "name": "Zona 01",
        "status": "INACTIVE"
      }
    ]
  }
]
```

---

### GET /devices/{device_id}

Obtém detalhes de um dispositivo específico.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
Igual ao dispositivo individual na resposta de `/devices`.

---

## Gerenciamento de Senha

### POST /devices/{device_id}/password

Salva a senha do dispositivo para funcionalidade de auto-sync.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Senha salva com sucesso"
}
```

**Notas:**
- A senha é armazenada criptografada em memória
- Necessária para status em tempo real via ISECNet

---

### DELETE /devices/{device_id}/password

Exclui a senha salva do dispositivo.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
```json
{
  "success": true,
  "message": "Senha excluída com sucesso"
}
```

---

## Endpoints de Controle de Alarme

### POST /alarm/{device_id}/arm

Arma uma partição.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "partition_id": 0,
  "mode": "away",
  "password": "senha-do-dispositivo"
}
```

**Opções de Modo:**
- `away`: Arma todas as zonas (total)
- `home`: Arma apenas perímetro (stay/parcial)

**Resposta:**
```json
{
  "success": true,
  "new_status": "armed_away"
}
```

**Erros:**
- `400`: Zonas abertas impedem o arme (retorna lista de zonas abertas)
- `401`: Senha inválida
- `404`: Dispositivo ou partição não encontrado

---

### POST /alarm/{device_id}/disarm

Desarma uma partição.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "partition_id": 0,
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "new_status": "disarmed"
}
```

---

### GET /alarm/{device_id}/status

Obtém status do alarme em tempo real via protocolo ISECNet.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Parâmetros de Query:**
- `password`: Senha do dispositivo (obrigatório)

**Resposta:**
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

Obtém status em tempo real usando senha salva.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
Igual a `/alarm/{device_id}/status`

**Erros:**
- `404`: Nenhuma senha salva para o dispositivo

---

### POST /alarm/{device_id}/bypass-zone

Bypass (anular) ou remover bypass de zonas.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "zone_indices": [33, 35],
  "bypass": true
}
```

**Campos:**
- `zone_indices`: Lista de índices de zonas (base 0) para bypass
- `bypass`: `true` para anular, `false` para remover anulação

**Resposta:**
```json
{
  "success": true,
  "device_id": 12345,
  "message": "Zones [33, 35] bypassed"
}
```

**Erros:**
- `400`: Bypass negado (sem permissão no painel, central armada)
- `503`: Central indisponível

**Notas:**
- V1 (AMT 2018 E Smart, etc.): envia bitmask com todas as zonas de uma vez. É um bitmask de estado completo — zonas não listadas terão bypass removido.
- V2 (AMT 8000, etc.): envia um comando por zona individualmente.

---

### POST /alarm/{device_id}/siren/off

Desliga a sirene sem alterar o estado de arme.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
```json
{
  "success": true,
  "message": "Sirene desligada"
}
```

---

## Endpoints de Zonas

### GET /devices/{device_id}/zones

Obtém todas as zonas com seus nomes amigáveis.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Resposta:**
```json
{
  "device_id": 12345,
  "zones": [
    {
      "index": 0,
      "name": "Zona 01",
      "friendly_name": "Porta da Frente",
      "is_open": false,
      "is_bypassed": false
    },
    {
      "index": 1,
      "name": "Zona 02",
      "friendly_name": null,
      "is_open": true,
      "is_bypassed": false
    }
  ]
}
```

---

### PUT /devices/{device_id}/zones/{zone_index}/friendly-name

Define um nome amigável para uma zona.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)
- `zone_index`: Índice da zona (base 0)

**Body da Requisição:**
```json
{
  "friendly_name": "Porta da Frente"
}
```

**Resposta:**
```json
{
  "success": true,
  "zone_index": 0,
  "friendly_name": "Porta da Frente"
}
```

---

### DELETE /devices/{device_id}/zones/{zone_index}/friendly-name

Exclui o nome amigável de uma zona.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)
- `zone_index`: Índice da zona (base 0)

**Resposta:**
```json
{
  "success": true,
  "message": "Nome amigável excluído"
}
```

---

## Endpoints de Eventos

### GET /events

Obtém histórico de eventos do alarme.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Query:**
- `limit`: Número máximo de eventos (padrão: 50, máx: 100)
- `offset`: Offset de paginação (padrão: 0)
- `since`: Data ISO 8601 para filtrar eventos (opcional)

**Resposta:**
```json
{
  "events": [
    {
      "id": 123456,
      "timestamp": "2024-01-24T10:30:00Z",
      "event_type": "alarm_triggered",
      "device_id": 12345,
      "partition_id": 0,
      "zone": {
        "id": 1,
        "name": "Zona 01",
        "friendly_name": "Porta da Frente"
      },
      "notification": {
        "code": 1000,
        "title": "Alarme Disparado",
        "message": "Zona 01 foi disparada"
      }
    }
  ],
  "total": 150,
  "offset": 0,
  "limit": 50
}
```

---

### GET /events/recent

Obtém os eventos mais recentes.

**Parâmetros de Query:**
- `count`: Número de eventos (padrão: 10, máx: 50)

**Resposta:**
```json
{
  "events": [...],
  "count": 10
}
```

---

### GET /events/stream

Stream de eventos em tempo real via Server-Sent Events (SSE).

**Parâmetros de Query (alternativo ao header):**
- `session_id`: Session ID

**Formato dos eventos SSE:**
```
event: connected
data: {"message": "Connected to event stream"}

event: alarm_event
data: {"event_type": "arm", "device_id": 12345, ...}

event: ping
data: {"timestamp": "2024-01-24T10:30:00Z"}
```

- O evento `ping` é enviado a cada 30s como keepalive
- A conexão é mantida aberta indefinidamente

---

## Endpoints de Eletrificador (Cerca Elétrica)

### POST /eletrificador/{device_id}/shock/on

Habilita o choque elétrico.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Choque habilitado"
}
```

---

### POST /eletrificador/{device_id}/shock/off

Desabilita o choque elétrico.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Choque desabilitado"
}
```

---

### POST /eletrificador/{device_id}/alarm/activate

Arma o alarme da cerca.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Alarme ativado"
}
```

---

### POST /eletrificador/{device_id}/alarm/deactivate

Desarma o alarme da cerca.

**Headers:**
- `X-Session-ID`: Seu session ID

**Parâmetros de Path:**
- `device_id`: ID do dispositivo (inteiro)

**Body da Requisição:**
```json
{
  "password": "senha-do-dispositivo"
}
```

**Resposta:**
```json
{
  "success": true,
  "message": "Alarme desativado"
}
```

---

## Respostas de Erro

Todos os erros seguem este formato:

```json
{
  "detail": "Descrição da mensagem de erro"
}
```

### Códigos de Status HTTP Comuns

| Código | Descrição |
|--------|-----------|
| 400    | Bad Request - Parâmetros inválidos |
| 401    | Unauthorized - Sessão ou credenciais inválidas |
| 404    | Not Found - Recurso não existe |
| 500    | Internal Server Error |

### Erro de Zonas Abertas

Quando o arme falha devido a zonas abertas (erro 0xE4):

```json
{
  "detail": "Não foi possível armar: zonas abertas",
  "error_code": "open_zones",
  "open_zones": [0, 2, 5]
}
```

---

## Documentação Interativa

Quando o middleware FastAPI estiver rodando, acesse:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
