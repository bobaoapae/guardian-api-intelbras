# Intelbras Guardian API + Integração Home Assistant

Integração completa para controlar sistemas de alarme Intelbras Guardian via Home Assistant.

[![Adicionar Repositório de Add-on](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbobaoapae%2Fguardian-api-intelbras)

## Opções de Instalação

### Opção 1: Add-on do Home Assistant (Recomendado para usuários do Supervisor)

Se você usa Home Assistant OS ou Supervised, pode instalar a API como add-on:

1. Clique no botão acima ou adicione o repositório manualmente:
   - **Configurações** → **Add-ons** → **Loja de Add-ons** → **⋮** (canto superior direito) → **Repositórios**
   - Adicione: `https://github.com/bobaoapae/guardian-api-intelbras`

2. Encontre "**Intelbras Guardian API**" na loja de add-ons e clique em **Instalar**

3. Inicie o add-on e acesse a Web UI em `http://[SEU_IP_HA]:8000`

4. Instale a integração do Home Assistant (veja abaixo)

### Opção 2: Script de Instalação Automática (Recomendado para Docker)

Para usuários de Docker ou Home Assistant Container, use o script de instalação interativo:

```bash
# Baixar e executar o instalador
curl -sSL https://raw.githubusercontent.com/bobaoapae/guardian-api-intelbras/main/install.sh -o install.sh
chmod +x install.sh
sudo ./install.sh
```

O script irá:
- Verificar pré-requisitos (Docker, Docker Compose)
- Detectar automaticamente seu Home Assistant
- Configurar diretórios e volumes
- Instalar a API e integração do HA
- Iniciar os serviços

**Outras opções do script:**
```bash
./install.sh --help       # Ver todas as opções
./install.sh -y           # Instalação não-interativa (aceita padrões)
./install.sh --update     # Atualizar instalação existente
./install.sh --uninstall  # Remover completamente
```

### Opção 3: Docker Compose Manual

Para instalação manual via Docker, veja [Instalação via Docker](#1-instalar-middleware-fastapi) abaixo.

### Opção 4: Python Manual

Para desenvolvimento ou configurações personalizadas, veja a seção [Desenvolvimento](#desenvolvimento).

---

## Arquitetura

Este projeto implementa uma arquitetura em 3 camadas:

```
┌─────────────────────────────────────────────────────────────────┐
│                  HOME ASSISTANT (Integração HACS)               │
│                                                                 │
│  - Config Flow (host:porta manual)                              │
│  - Coordinator (polling 30s)                                    │
│  - Entidades:                                                   │
│    - alarm_control_panel (uma por partição)                     │
│    - binary_sensor (um por zona)                                │
│    - sensor (último evento)                                     │
│    - switch (choque/alarme do eletrificador)                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP REST
┌───────────────────────────▼─────────────────────────────────────┐
│                  FASTAPI MIDDLEWARE (Container)                 │
│                                                                 │
│  - Autenticação OAuth 2.0 com Intelbras Cloud                   │
│  - Refresh automático de token                                  │
│  - Protocolo ISECNet (comunicação direta com a central)         │
│  - Cache e gerenciamento de estado                              │
│  - Gerenciamento de nomes amigáveis das zonas                   │
│  - Armazenamento de senha do dispositivo para auto-sync         │
│  - Web UI para testes e gerenciamento                           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS + ISECNet
┌───────────────────────────▼─────────────────────────────────────┐
│  INFRAESTRUTURA INTELBRAS                                       │
│                                                                 │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐│
│  │  API Cloud          │    │  Receptor IP (Relay)            ││
│  │  api-guardian...    │    │  Encaminha comandos ISECNet     ││
│  │  :8443              │    │  para a central de alarme       ││
│  └──────────┬──────────┘    └─────────────┬───────────────────┘│
│             │                             │                     │
│             └──────────────┬──────────────┘                     │
│                            │                                    │
│  ┌─────────────────────────▼───────────────────────────────────┐│
│  │            CENTRAL DE ALARME (AMT, ANM, etc)                ││
│  │                                                             ││
│  │  - Partições (áreas que podem ser armadas independentemente)││
│  │  - Zonas (sensores: portas, janelas, movimento, etc)        ││
│  │  - Protocolo ISECNet V1/V2 para comunicação                 ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Como Funciona

### Fluxo de Comunicação

1. **Autenticação**: Usuário faz login com credenciais da conta Intelbras. FastAPI obtém tokens OAuth 2.0 do Intelbras Cloud.

2. **Descoberta de Dispositivos**: FastAPI consulta a API do Intelbras Cloud para listar centrais de alarme registradas com suas partições e zonas.

3. **Status em Tempo Real (Protocolo ISECNet)**:
   - FastAPI conecta ao Receptor IP da Intelbras
   - Envia comandos ISECNet diretamente para a central de alarme
   - Recebe status em tempo real: estado armado/desarmado, zonas abertas, alarmes disparados
   - Isso evita a latência da nuvem para atualizações de status

4. **Comandos de Armar/Desarmar**:
   - Home Assistant envia comando para FastAPI
   - FastAPI envia comando ISECNet via Receptor IP para a central
   - Central executa o comando e retorna resultado
   - Status é atualizado imediatamente

5. **Monitoramento de Zonas**:
   - ISECNet fornece status em tempo real das zonas (aberta/fechada)
   - Nomes amigáveis podem ser atribuídos às zonas via FastAPI
   - Sensores binários no Home Assistant refletem o estado das zonas

### Protocolo ISECNet

ISECNet é o protocolo proprietário da Intelbras para comunicação direta com centrais de alarme:

- **Versão 1**: Comandos básicos (armar, desarmar, status)
- **Versão 2**: Recursos estendidos (nomes de zonas, controle de PGM)

O protocolo usa:
- Conexão TCP via Receptor IP da Intelbras
- Formato de pacote binário com validação CRC
- Autenticação baseada em senha por dispositivo
- Criptografia para dados sensíveis

### Dispositivos Suportados

- **Centrais de Alarme**: AMT 2008, AMT 2010, AMT 2018, AMT 4010, série ANM
- **Eletrificadores**: ELC 5001, ELC 5002

## Funcionalidades

### Painel de Controle de Alarme
- Armar/desarmar partições
- Modos de arme: Ausente (total) e Em Casa (stay/perímetro)
- Detecção de estado disparado
- Status em tempo real via ISECNet

### Sensores de Zona (Sensores Binários)
- Status em tempo real aberto/fechado
- Nomes amigáveis personalizáveis
- Classe de dispositivo baseada no tipo de zona (porta, janela, movimento, fumaça, etc.)
- Atributo de status de bypass

### Controle de Eletrificador (Switches)
- **Switch de Choque**: Habilitar/desabilitar choque elétrico
- **Switch de Alarme**: Armar/desarmar alarme da cerca

### Sensor de Evento
- Informação do último evento
- Atributos do histórico de eventos

## Início Rápido

### Pré-requisitos

- Docker e Docker Compose
- Home Assistant 2023.x ou posterior
- Sistema de alarme Intelbras Guardian com acesso à nuvem
- Conta Intelbras (email + senha)
- Senha do dispositivo (programada na central de alarme)

### 1. Instalar Middleware FastAPI

```bash
# Clonar o repositório
git clone https://github.com/bobaoapae/guardian-api-intelbras.git
cd guardian-api-intelbras

# Configurar ambiente
cd intelbras-guardian-api
cp .env.example .env
# Edite .env e adicione a URL do seu Home Assistant em CORS_ORIGINS

# Iniciar o container
cd ../docker
docker-compose up -d

# Verificar se está rodando
curl http://localhost:8000/api/v1/health
```

### 2. Acessar Web UI

Abra http://localhost:8000 no navegador para:
- Testar login com suas credenciais Intelbras
- Ver dispositivos e seus status
- Salvar senhas dos dispositivos para auto-sync
- Configurar nomes amigáveis das zonas
- Testar comandos de armar/desarmar

### 3. Instalar Integração do Home Assistant

```bash
# Copiar integração para o Home Assistant
cp -r home_assistant/custom_components/intelbras_guardian \
      /config/custom_components/

# Reiniciar Home Assistant
```

### 4. Configurar Integração

1. Vá em **Configurações** → **Dispositivos e Serviços** → **Adicionar Integração**
2. Procure por "**Intelbras Guardian**"
3. Preencha:
   - **Email**: Email da sua conta Intelbras
   - **Senha**: Senha da sua conta Intelbras
   - **Host FastAPI**: IP do container FastAPI (ex: 192.168.1.100)
   - **Porta FastAPI**: 8000 (padrão)

### 5. Salvar Senha do Dispositivo

Para status em tempo real via ISECNet:

**Opção A - Via Home Assistant:**
- Configurações → Dispositivos e Serviços → Intelbras Guardian → Configurar → Configurar Senha do Dispositivo

**Opção B - Via Web UI:**
1. Abra a Web UI do FastAPI (http://localhost:8000)
2. Faça login com suas credenciais Intelbras
3. Clique em "Salvar Senha" no seu dispositivo
4. Insira a senha do dispositivo (configurada na central de alarme)
5. O status agora sincronizará automaticamente

## Endpoints da API

### Autenticação
- `POST /api/v1/auth/login` - Login com credenciais Intelbras
- `POST /api/v1/auth/logout` - Logout e invalidar sessão
- `GET /api/v1/auth/session` - Obter informações da sessão atual

### Dispositivos
- `GET /api/v1/devices` - Listar todas as centrais de alarme
- `GET /api/v1/devices/{id}` - Obter detalhes do dispositivo

### Controle de Alarme
- `POST /api/v1/alarm/{device_id}/arm` - Armar partição
- `POST /api/v1/alarm/{device_id}/disarm` - Desarmar partição
- `GET /api/v1/alarm/{device_id}/status` - Obter status em tempo real (requer senha)
- `GET /api/v1/alarm/{device_id}/status/auto` - Obter status usando senha salva

### Gerenciamento de Senha
- `POST /api/v1/devices/{device_id}/password` - Salvar senha do dispositivo
- `DELETE /api/v1/devices/{device_id}/password` - Excluir senha salva

### Zonas
- `GET /api/v1/devices/{device_id}/zones` - Obter zonas com nomes amigáveis
- `PUT /api/v1/devices/{device_id}/zones/{zone_index}/friendly-name` - Definir nome amigável
- `DELETE /api/v1/devices/{device_id}/zones/{zone_index}/friendly-name` - Excluir nome amigável

### Eventos
- `GET /api/v1/events` - Obter histórico de eventos do alarme

### Eletrificador
- `POST /api/v1/eletrificador/{device_id}/shock/on` - Habilitar choque
- `POST /api/v1/eletrificador/{device_id}/shock/off` - Desabilitar choque
- `POST /api/v1/eletrificador/{device_id}/alarm/activate` - Armar alarme
- `POST /api/v1/eletrificador/{device_id}/alarm/deactivate` - Desarmar alarme

## Estrutura do Projeto

```
guardian-api-intelbras/
├── intelbras-guardian-api/           # API FastAPI + Add-on HA
│   ├── app/                          # Código da aplicação
│   │   ├── main.py                   # Ponto de entrada da aplicação
│   │   ├── core/                     # Config, exceções, segurança
│   │   ├── models/                   # Modelos Pydantic
│   │   ├── services/                 # Lógica de negócio
│   │   │   ├── guardian_client.py    # Cliente da API Intelbras Cloud
│   │   │   ├── auth_service.py       # Autenticação OAuth 2.0
│   │   │   ├── state_manager.py      # Gerenciamento de estado/cache
│   │   │   └── isecnet_protocol.py   # Implementação ISECNet
│   │   ├── api/v1/                   # Endpoints REST
│   │   └── static/                   # Web UI
│   ├── tests/                        # Testes
│   ├── config.yaml                   # Configuração do add-on HA
│   ├── Dockerfile                    # Build da imagem (add-on)
│   ├── build.yaml                    # Build multi-arquitetura
│   ├── run.sh                        # Script de inicialização
│   └── requirements.txt              # Dependências Python
├── docker/                           # Docker Compose Standalone
│   ├── Dockerfile                    # Build da imagem (standalone)
│   └── docker-compose.yml
├── home_assistant/                   # Integração Home Assistant
│   └── custom_components/
│       └── intelbras_guardian/
│           ├── __init__.py
│           ├── manifest.json
│           ├── config_flow.py
│           ├── coordinator.py
│           ├── api_client.py
│           ├── alarm_control_panel.py
│           ├── binary_sensor.py
│           ├── sensor.py
│           ├── switch.py
│           └── const.py
└── docs/                             # Documentação
```

## Variáveis de Ambiente

### FastAPI (.env)

```env
# API Intelbras (não alterar)
INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa

# Servidor
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO

# CORS (adicione a URL do seu Home Assistant)
CORS_ORIGINS=http://localhost:8123,http://homeassistant.local:8123

# Timeouts
HTTP_TIMEOUT=30
TOKEN_REFRESH_BUFFER=300
EVENT_POLL_INTERVAL=30
```

## Considerações de Segurança

- **Credenciais**: Nunca faça commit de arquivos `.env` com credenciais
- **HTTPS**: Use um proxy reverso com SSL em produção
- **CORS**: Restrinja `CORS_ORIGINS` apenas a domínios confiáveis
- **Senhas de Dispositivo**: Armazenadas criptografadas em memória, não persistidas em disco
- **Logs**: Dados sensíveis (senhas, tokens) são automaticamente filtrados dos logs

## Solução de Problemas

### "Não foi possível conectar ao middleware FastAPI"
- Verifique se o container FastAPI está rodando: `docker ps`
- Verifique a configuração de host/porta
- Verifique regras de firewall

### "Credenciais inválidas"
- Verifique seu email e senha da conta Intelbras
- Tente fazer login em https://guardian.intelbras.com.br

### Armar/Desarmar não funciona
- Verifique se a senha do dispositivo está salva corretamente
- Verifique se o dispositivo está online no app Intelbras
- Verifique a conexão ISECNet nos logs do FastAPI

### Zonas mostrando status errado
- Salve a senha do dispositivo para status em tempo real via ISECNet
- Verifique se as zonas estão configuradas corretamente na central de alarme

## Desenvolvimento

### Executar FastAPI localmente

```bash
cd intelbras-guardian-api
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Swagger UI

Acesse documentação interativa da API em: http://localhost:8000/docs

## Roadmap

- [ ] Notificações push via Firebase Cloud Messaging
- [ ] Controle de PGM (saída programável)
- [ ] Funcionalidade de bypass de zona
- [ ] Suporte a ISECNet local (sem relay da nuvem)
- [ ] Integração com Google Assistant / Alexa

## Contribuindo

Contribuições são bem-vindas! Por favor:

1. Teste com seu sistema de alarme Intelbras
2. Reporte issues com logs detalhados
3. Envie pull requests com melhorias

## Licença

Licença MIT - veja o arquivo [LICENSE](LICENSE) para detalhes.

## Aviso Legal

**ESTE SOFTWARE É FORNECIDO "COMO ESTÁ", SEM GARANTIA DE QUALQUER TIPO.**

- Este projeto **NÃO É afiliado, endossado ou associado à Intelbras** de nenhuma forma
- O uso deste software é **inteiramente por sua conta e risco**
- Os autores **não são responsáveis** por qualquer dano, perda ou problema de segurança que possa resultar do uso deste software
- Este software interage com sistemas de segurança - **uso inadequado pode comprometer sua segurança**
- Sempre certifique-se de que seu sistema de alarme está devidamente configurado e testado
- Não dependa exclusivamente desta integração para aplicações críticas de segurança

Ao usar este software, você reconhece que entende e aceita estes termos.

## Suporte

- **Issues**: https://github.com/bobaoapae/guardian-api-intelbras/issues
- **Documentação**: Verifique a pasta `/docs`

---

**Feito para a comunidade Home Assistant**
