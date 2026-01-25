# Plano do Script de Instalação Automatizada

## Visão Geral

Script interativo (`install.sh`) que guia o usuário pelo processo completo de instalação da integração Intelbras Guardian no Home Assistant rodando em Docker.

---

## Fluxo do Script

```
┌─────────────────────────────────────────────────────────────────┐
│                        INÍCIO                                   │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. VERIFICAÇÃO DE PRÉ-REQUISITOS                               │
│     - Docker instalado?                                         │
│     - Docker Compose instalado?                                 │
│     - Git instalado? (opcional, pode baixar .zip)               │
│     - Curl instalado?                                           │
│     - Usuário tem permissão docker?                             │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. DETECÇÃO DO AMBIENTE                                        │
│     - Detectar Home Assistant Docker rodando                    │
│     - Encontrar pasta de configuração do HA (/config)           │
│     - Perguntar se quer usar detecção ou informar manualmente   │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. ESCOLHA DO DIRETÓRIO DE INSTALAÇÃO                          │
│     - Sugerir: /opt/intelbras-guardian                          │
│     - Permitir customização                                     │
│     - Criar diretório se não existir                            │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. DOWNLOAD DOS ARQUIVOS                                       │
│     - Opção A: git clone (se git disponível)                    │
│     - Opção B: wget/curl do .zip do GitHub + unzip              │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. CONFIGURAÇÃO DO VOLUME DOCKER                               │
│     - Perguntar local para dados persistentes                   │
│     - Sugerir: /var/lib/intelbras-guardian                      │
│     - Criar diretório com permissões corretas                   │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  6. CONFIGURAÇÃO DO CORS                                        │
│     - Detectar IP do Home Assistant                             │
│     - Perguntar porta do HA (padrão: 8123)                      │
│     - Gerar lista de CORS_ORIGINS                               │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  7. ESCOLHA DO MODO DE DEPLOY                                   │
│     A) Standalone (docker-compose próprio)                      │
│     B) Integrar ao docker-compose do HA existente               │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  8. GERAÇÃO DO DOCKER-COMPOSE                                   │
│     - Criar/atualizar docker-compose.yml                        │
│     - Configurar volume com caminho escolhido                   │
│     - Configurar rede (bridge ou rede do HA)                    │
│     - Configurar restart policy                                 │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  9. INSTALAÇÃO DA INTEGRAÇÃO HOME ASSISTANT                     │
│     - Copiar custom_components/intelbras_guardian para HA       │
│     - Verificar permissões dos arquivos                         │
│     - Informar que HA precisa reiniciar                         │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  10. INICIAR SERVIÇOS                                           │
│      - docker-compose up -d                                     │
│      - Aguardar container ficar healthy                         │
│      - Testar endpoint /api/v1/health                           │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  11. CONFIGURAÇÃO DO SYSTEMD (OPCIONAL)                         │
│      - Perguntar se quer criar serviço systemd                  │
│      - Criar /etc/systemd/system/intelbras-guardian.service     │
│      - Habilitar auto-start no boot                             │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  12. RESUMO E PRÓXIMOS PASSOS                                   │
│      - Mostrar URLs de acesso                                   │
│      - Instruções para reiniciar HA                             │
│      - Como adicionar a integração no HA                        │
│      - Comandos úteis (logs, restart, etc)                      │
└─────────────────────────┬───────────────────────────────────────┘
                          ▼
                        FIM
```

---

## Detalhamento das Etapas

### 1. Verificação de Pré-requisitos

```bash
# Verificações obrigatórias
- command -v docker          → "Docker não encontrado"
- command -v docker-compose  → "Docker Compose não encontrado" (ou docker compose)
- command -v curl            → "Curl não encontrado"
- docker info                → "Sem permissão para usar Docker"

# Verificações opcionais
- command -v git             → Usar wget como fallback
- command -v unzip           → Necessário se usar wget
```

### 2. Detecção do Ambiente

```bash
# Tentar detectar container do Home Assistant
docker ps --filter "name=homeassistant" --format "{{.Names}}"
docker ps --filter "ancestor=homeassistant/home-assistant" --format "{{.Names}}"

# Encontrar volume/bind mount do /config
docker inspect <container> --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}'

# Exemplo de saída: /home/usuario/homeassistant
```

### 3. Escolha do Diretório

```bash
# Valores padrão
INSTALL_DIR="/opt/intelbras-guardian"
DATA_DIR="/var/lib/intelbras-guardian"

# Perguntas interativas
read -p "Diretório de instalação [$INSTALL_DIR]: " user_install_dir
read -p "Diretório de dados [$DATA_DIR]: " user_data_dir
```

### 4. Download dos Arquivos

```bash
# Opção A: Git
git clone https://github.com/bobaoapae/guardian-api-intelbras.git "$INSTALL_DIR"

# Opção B: Wget/Curl
curl -L https://github.com/bobaoapae/guardian-api-intelbras/archive/refs/heads/main.zip -o /tmp/guardian.zip
unzip /tmp/guardian.zip -d /tmp/
mv /tmp/guardian-api-intelbras-main "$INSTALL_DIR"
```

### 5. Configuração do Volume

```bash
# Criar diretório de dados
mkdir -p "$DATA_DIR"
chmod 755 "$DATA_DIR"

# Se rodar como usuário específico
# chown 1000:1000 "$DATA_DIR"
```

### 6. Configuração do CORS

```bash
# Detectar IPs
HA_IP=$(hostname -I | awk '{print $1}')
# Ou do container
HA_IP=$(docker inspect homeassistant --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')

# Gerar CORS
CORS_ORIGINS="http://localhost:8123,http://${HA_IP}:8123,http://homeassistant.local:8123"
```

### 7. Modo de Deploy

```bash
echo "Como deseja instalar?"
echo "1) Standalone (docker-compose separado)"
echo "2) Adicionar ao docker-compose existente do Home Assistant"
read -p "Escolha [1]: " deploy_mode
```

### 8. Geração do Docker-Compose

**Modo Standalone:**
```yaml
version: '3.8'
services:
  intelbras-guardian-api:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: intelbras-guardian-api
    ports:
      - "8000:8000"
    environment:
      - INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
      - INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
      - INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa
      - CORS_ORIGINS=${CORS_ORIGINS}
      - LOG_LEVEL=INFO
    volumes:
      - ${DATA_DIR}:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

**Modo Integrado (adicionar ao existente):**
```bash
# Fazer backup
cp docker-compose.yml docker-compose.yml.bak

# Adicionar serviço via yq ou sed
# Ou instruir usuário a adicionar manualmente
```

### 9. Instalação da Integração HA

```bash
# Caminho detectado ou informado
HA_CONFIG="/home/usuario/homeassistant"

# Criar diretório custom_components se não existir
mkdir -p "$HA_CONFIG/custom_components"

# Copiar integração
cp -r "$INSTALL_DIR/home_assistant/custom_components/intelbras_guardian" \
      "$HA_CONFIG/custom_components/"

# Ajustar permissões (se necessário)
# chown -R 1000:1000 "$HA_CONFIG/custom_components/intelbras_guardian"
```

### 10. Iniciar Serviços

```bash
cd "$INSTALL_DIR"
docker-compose up -d --build

# Aguardar healthy
echo "Aguardando serviço iniciar..."
for i in {1..30}; do
    if curl -s http://localhost:8000/api/v1/health | grep -q "healthy"; then
        echo "✓ Serviço iniciado com sucesso!"
        break
    fi
    sleep 2
done
```

### 11. Serviço Systemd (Opcional)

```ini
[Unit]
Description=Intelbras Guardian API
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/intelbras-guardian
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

### 12. Resumo Final

```
╔══════════════════════════════════════════════════════════════════╗
║           INSTALAÇÃO CONCLUÍDA COM SUCESSO!                      ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  API FastAPI:                                                    ║
║    • URL: http://192.168.1.100:8000                              ║
║    • Swagger: http://192.168.1.100:8000/docs                     ║
║    • Health: http://192.168.1.100:8000/api/v1/health             ║
║                                                                  ║
║  Arquivos instalados:                                            ║
║    • API: /opt/intelbras-guardian                                ║
║    • Dados: /var/lib/intelbras-guardian                          ║
║    • Integração HA: /home/user/ha/custom_components/             ║
║                                                                  ║
║  PRÓXIMOS PASSOS:                                                ║
║                                                                  ║
║  1. Reiniciar Home Assistant:                                    ║
║     docker restart homeassistant                                 ║
║                                                                  ║
║  2. Adicionar integração no HA:                                  ║
║     Configurações → Dispositivos e Serviços →                    ║
║     Adicionar Integração → "Intelbras Guardian"                  ║
║                                                                  ║
║  3. Configurar:                                                  ║
║     • Email: seu_email@intelbras.com                             ║
║     • Senha: sua_senha                                           ║
║     • Host FastAPI: 192.168.1.100                                ║
║     • Porta: 8000                                                ║
║                                                                  ║
║  COMANDOS ÚTEIS:                                                 ║
║    • Ver logs: docker logs -f intelbras-guardian-api             ║
║    • Reiniciar: docker restart intelbras-guardian-api            ║
║    • Parar: cd /opt/intelbras-guardian && docker-compose down    ║
║    • Atualizar: git pull && docker-compose up -d --build         ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Funcionalidades Adicionais

### Flags de Linha de Comando

```bash
./install.sh [opções]

Opções:
  -h, --help              Mostra esta ajuda
  -y, --yes               Aceita todos os padrões (não-interativo)
  -d, --install-dir DIR   Define diretório de instalação
  -c, --config-dir DIR    Define diretório do config do HA
  --no-integration        Não instalar integração do HA
  --no-systemd            Não criar serviço systemd
  --uninstall             Desinstalar completamente
```

### Modo de Desinstalação

```bash
./install.sh --uninstall

# Ações:
# 1. Parar e remover container
# 2. Remover imagem Docker
# 3. Remover arquivos de instalação
# 4. Remover integração do HA
# 5. Remover serviço systemd
# 6. (Opcional) Remover dados persistentes
```

### Modo de Atualização

```bash
./install.sh --update

# Ações:
# 1. git pull (ou baixar novo .zip)
# 2. docker-compose build
# 3. docker-compose up -d
# 4. Atualizar integração do HA
# 5. Sugerir reiniciar HA
```

---

## Tratamento de Erros

| Erro | Ação |
|------|------|
| Docker não instalado | Mostrar instruções de instalação |
| Sem permissão Docker | Sugerir `sudo usermod -aG docker $USER` |
| Porta 8000 em uso | Perguntar porta alternativa |
| HA não detectado | Perguntar caminho manualmente |
| Falha no git clone | Tentar download via curl |
| Container não inicia | Mostrar logs e sugerir correções |

---

## Compatibilidade

### Sistemas Testados
- Ubuntu 20.04 / 22.04 / 24.04
- Debian 11 / 12
- Raspberry Pi OS (64-bit)
- Alpine Linux (para containers)

### Requisitos Mínimos
- Docker 20.10+
- Docker Compose 2.0+ (ou docker-compose 1.29+)
- 256MB RAM disponível
- 500MB espaço em disco

---

## Estrutura do Script

```
install.sh
├── Variáveis globais e cores
├── Funções utilitárias
│   ├── print_header()
│   ├── print_success()
│   ├── print_error()
│   ├── print_warning()
│   ├── ask_yes_no()
│   └── ask_input()
├── Funções de verificação
│   ├── check_prerequisites()
│   ├── check_docker_permission()
│   └── detect_ha_container()
├── Funções de instalação
│   ├── download_files()
│   ├── setup_directories()
│   ├── configure_docker_compose()
│   ├── install_ha_integration()
│   ├── start_services()
│   └── setup_systemd()
├── Funções de desinstalação
│   └── uninstall()
├── Função principal
│   └── main()
└── Ponto de entrada
    └── parse_args() + main()
```

---

## Aprovação

Esse plano cobre:
- [x] Verificação de pré-requisitos
- [x] Detecção automática do Home Assistant
- [x] Configuração interativa de diretórios
- [x] Download dos arquivos (git ou zip)
- [x] Configuração de volumes Docker
- [x] Configuração de CORS
- [x] Dois modos de deploy (standalone/integrado)
- [x] Instalação da integração do HA
- [x] Inicialização e verificação de saúde
- [x] Serviço systemd opcional
- [x] Resumo com próximos passos
- [x] Modo de desinstalação
- [x] Modo de atualização
- [x] Tratamento de erros

**Aguardando aprovação para implementar o script.**
