# Guia de Instalação

Instruções completas para instalar a integração Intelbras Guardian.

## Pré-requisitos

- Docker e Docker Compose instalados
- Home Assistant 2023.x ou posterior
- Acesso à rede para serviços cloud da Intelbras
- Conta Intelbras Guardian (email + senha)
- Senha do painel de alarme

## Visão Geral da Arquitetura

```
┌────────────────────┐     ┌────────────────────┐     ┌──────────────────┐
│   Home Assistant   │────▶│  Container FastAPI │────▶│  Intelbras Cloud │
│   (seu servidor)   │     │  (Docker)          │     │  + Receptor IP   │
└────────────────────┘     └────────────────────┘     └──────────────────┘
```

O container do middleware FastAPI pode rodar:
- Na mesma máquina do Home Assistant
- Em um servidor separado na sua rede
- Em um VPS/servidor cloud (não recomendado por latência)

## Opção 1: Add-on do Home Assistant (Recomendado)

Se você usa Home Assistant OS ou Supervised:

### Passo 1: Adicionar Repositório

Clique no botão abaixo ou adicione manualmente:

[![Adicionar Repositório](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbobaoapae%2Fguardian-api-intelbras)

Manualmente: **Configurações** → **Add-ons** → **Loja de Add-ons** → **⋮** → **Repositórios** → Adicione `https://github.com/bobaoapae/guardian-api-intelbras`

### Passo 2: Instalar Add-on

1. Encontre "**Intelbras Guardian API**" na loja
2. Clique em **Instalar**
3. Inicie o add-on
4. Acesse a Web UI em `http://[SEU_IP_HA]:8000`

## Opção 2: Docker Compose (Standalone)

### Passo 1: Clonar Repositório

```bash
git clone https://github.com/bobaoapae/guardian-api-intelbras.git
cd guardian-api-intelbras
```

### Passo 2: Configurar Ambiente

```bash
cd fastapi_middleware
cp .env.example .env
```

Edite `.env` com suas configurações:

```env
# Servidor
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO

# CORS - Adicione a URL do seu Home Assistant
CORS_ORIGINS=http://localhost:8123,http://192.168.1.100:8123,http://homeassistant.local:8123

# API Intelbras (não altere estes valores)
INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa
```

### Passo 3: Iniciar Container

```bash
cd ../docker
docker-compose up -d
```

### Passo 4: Verificar Instalação

```bash
# Verificar se o container está rodando
docker ps

# Verificar endpoint de health
curl http://localhost:8000/api/v1/health

# Verificar logs
docker logs intelbras-guardian-api
```

### Passo 5: Acessar Web UI

Abra http://localhost:8000 no navegador para:
- Testar login com suas credenciais
- Verificar se os dispositivos são listados
- Salvar senhas dos dispositivos
- Testar funcionalidade de armar/desarmar

## Opção 3: Docker Manual

```bash
# Build da imagem
cd fastapi_middleware
docker build -t intelbras-guardian-api -f ../docker/Dockerfile .

# Rodar container
docker run -d \
  --name intelbras-guardian-api \
  -p 8000:8000 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e CORS_ORIGINS="http://localhost:8123,http://homeassistant.local:8123" \
  --restart unless-stopped \
  intelbras-guardian-api
```

## Opção 4: Python Direto (Desenvolvimento)

```bash
cd fastapi_middleware

# Criar ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou: venv\Scripts\activate  # Windows

# Instalar dependências
pip install -r requirements.txt

# Configurar ambiente
cp .env.example .env
# Edite .env conforme necessário

# Rodar servidor
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuração da Integração Home Assistant

### Passo 1: Copiar Arquivos da Integração

```bash
# Se o Home Assistant está na mesma máquina
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Ou via SSH/SCP
scp -r home_assistant/custom_components/intelbras_guardian \
      usuario@homeassistant:/config/custom_components/
```

### Passo 2: Reiniciar Home Assistant

Vá em **Configurações** → **Sistema** → **Reiniciar**

Ou via CLI:
```bash
ha core restart
```

### Passo 3: Adicionar Integração

1. Vá em **Configurações** → **Dispositivos e Serviços**
2. Clique em **Adicionar Integração**
3. Procure por "**Intelbras Guardian**"
4. Preencha a configuração:
   - **Email**: Email da sua conta Intelbras
   - **Senha**: Senha da sua conta Intelbras
   - **Host FastAPI**: Endereço IP do container FastAPI
   - **Porta FastAPI**: 8000 (padrão)

### Passo 4: Verificar Entidades

Após a configuração, você deve ver:
- `alarm_control_panel.intelbras_guardian_*` - Uma por partição
- `binary_sensor.intelbras_guardian_*` - Um por zona
- `sensor.intelbras_guardian_ultimo_evento` - Sensor de eventos
- `switch.intelbras_guardian_*` - Para eletrificadores (se aplicável)

## Considerações para Produção

### HTTPS com Proxy Reverso (nginx)

Para produção, use nginx como proxy reverso com SSL:

```nginx
server {
    listen 443 ssl;
    server_name guardian-api.seudominio.com;

    ssl_certificate /etc/ssl/certs/seu-cert.pem;
    ssl_certificate_key /etc/ssl/private/sua-chave.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Serviço Systemd (sem Docker)

Crie `/etc/systemd/system/intelbras-guardian.service`:

```ini
[Unit]
Description=Intelbras Guardian API
After=network.target

[Service]
Type=simple
User=guardian
WorkingDirectory=/opt/intelbras-guardian/fastapi_middleware
Environment=PATH=/opt/intelbras-guardian/venv/bin
ExecStart=/opt/intelbras-guardian/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Habilite e inicie:
```bash
sudo systemctl enable intelbras-guardian
sudo systemctl start intelbras-guardian
```

### Configuração de Firewall

Abra apenas a porta necessária:

```bash
# UFW (Ubuntu)
sudo ufw allow from 192.168.1.0/24 to any port 8000

# firewalld (CentOS/Fedora)
sudo firewall-cmd --zone=internal --add-port=8000/tcp --permanent
sudo firewall-cmd --reload
```

### Limites de Recursos (Docker)

Adicione ao `docker-compose.yml`:

```yaml
services:
  fastapi:
    # ... configuração existente ...
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
        reservations:
          cpus: '0.1'
          memory: 128M
```

## Atualização

### Docker Compose

```bash
cd guardian-api-intelbras
git pull
cd docker
docker-compose build
docker-compose up -d
```

### Integração Home Assistant

```bash
# Remover arquivos antigos
rm -rf /config/custom_components/intelbras_guardian

# Copiar novos arquivos
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Reiniciar Home Assistant
```

## Solução de Problemas

### Container não inicia

Verifique os logs:
```bash
docker logs intelbras-guardian-api
```

Problemas comuns:
- Porta 8000 já em uso
- Variáveis de ambiente inválidas
- Problemas de conectividade de rede

### Não consegue conectar do Home Assistant

1. Verifique se o container está rodando: `docker ps`
2. Verifique o IP do container: `docker inspect intelbras-guardian-api | grep IPAddress`
3. Teste da máquina do HA: `curl http://ip-do-container:8000/api/v1/health`
4. Verifique regras de firewall

### Falha na autenticação

1. Verifique se as credenciais funcionam em https://guardian.intelbras.com.br
2. Verifique os logs do FastAPI para erro detalhado
3. Certifique-se de que os serviços cloud da Intelbras estão acessíveis

### Armar/Desarmar não funciona

1. Verifique se a senha do dispositivo está correta (mesma do app oficial)
2. Verifique a conexão ISECNet nos logs
3. Certifique-se de que o dispositivo está online e conectado

## Backup e Recuperação

### Backup

Dados importantes para backup:
- Arquivo `.env` (contém configuração)
- Config entry do Home Assistant (armazenado automaticamente)

Nota: Senhas de dispositivos e nomes amigáveis de zonas são armazenados em memória e serão perdidos ao reiniciar o container. Salve-os novamente via Web UI após reiniciar.

### Recuperação

1. Restaure o arquivo `.env`
2. Inicie o container
3. Re-autentique no Home Assistant
4. Salve novamente as senhas dos dispositivos via Web UI

## Monitoramento

### Health Check

O container inclui health check. Monitore com:

```bash
docker inspect --format='{{.State.Health.Status}}' intelbras-guardian-api
```

### Logs

Visualize logs em tempo real:
```bash
docker logs -f intelbras-guardian-api
```

### Métricas (Opcional)

Para monitoramento em produção, considere adicionar:
- Endpoint de métricas Prometheus
- Dashboards Grafana
- Regras de alerta para falhas
