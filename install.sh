#!/bin/bash

#===============================================================================
#
#          FILE: install.sh
#
#         USAGE: ./install.sh [opções]
#
#   DESCRIPTION: Script de instalação automatizada da integração
#                Intelbras Guardian para Home Assistant
#
#       OPTIONS: -h, --help        Mostra ajuda
#                -y, --yes         Modo não-interativo (aceita padrões)
#                -d, --dir DIR     Diretório de instalação
#                -c, --config DIR  Diretório config do HA
#                --uninstall       Desinstalar
#                --update          Atualizar instalação existente
#
#  REQUIREMENTS: Docker, Docker Compose, curl
#
#        AUTHOR: Intelbras Guardian Community
#       VERSION: 1.0.0
#
#===============================================================================

set -e

#-------------------------------------------------------------------------------
# Variáveis Globais
#-------------------------------------------------------------------------------

VERSION="1.0.6"
GITHUB_REPO="https://github.com/bobaoapae/guardian-api-intelbras"
GITHUB_ZIP="https://github.com/bobaoapae/guardian-api-intelbras/archive/refs/heads/main.zip"

# Diretórios padrão
DEFAULT_INSTALL_DIR="/opt/intelbras-guardian"
DEFAULT_DATA_DIR="/var/lib/intelbras-guardian"
DEFAULT_HA_PORT="8123"
DEFAULT_API_PORT="8000"

# Variáveis de instalação
INSTALL_DIR=""
DATA_DIR=""
HA_CONFIG_DIR=""
HA_CONTAINER=""
HA_IP=""
API_PORT=""
CORS_ORIGINS=""
NON_INTERACTIVE=false
DEPLOY_MODE="standalone"

#-------------------------------------------------------------------------------
# Cores para output
#-------------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

#-------------------------------------------------------------------------------
# Funções de Output
#-------------------------------------------------------------------------------

print_header() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║                                                                  ║"
    echo "║     ██╗███╗   ██╗████████╗███████╗██╗     ██████╗ ██████╗  █████╗ ███████╗   ║"
    echo "║     ██║████╗  ██║╚══██╔══╝██╔════╝██║     ██╔══██╗██╔══██╗██╔══██╗██╔════╝   ║"
    echo "║     ██║██╔██╗ ██║   ██║   █████╗  ██║     ██████╔╝██████╔╝███████║███████╗   ║"
    echo "║     ██║██║╚██╗██║   ██║   ██╔══╝  ██║     ██╔══██╗██╔══██╗██╔══██║╚════██║   ║"
    echo "║     ██║██║ ╚████║   ██║   ███████╗███████╗██████╔╝██║  ██║██║  ██║███████║   ║"
    echo "║     ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝   ║"
    echo "║                                                                  ║"
    echo "║              GUARDIAN - Instalador v${VERSION}                       ║"
    echo "║                                                                  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_step() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${CYAN}ℹ $1${NC}"
}

#-------------------------------------------------------------------------------
# Funções Utilitárias
#-------------------------------------------------------------------------------

get_host_ip() {
    local ip=""

    # Método 1: ip route (mais confiável no Linux)
    if command -v ip &> /dev/null; then
        ip=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
    fi

    # Método 2: hostname -I
    if [ -z "$ip" ] && command -v hostname &> /dev/null; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi

    # Método 3: ifconfig
    if [ -z "$ip" ] && command -v ifconfig &> /dev/null; then
        ip=$(ifconfig 2>/dev/null | awk '/inet / && !/127.0.0.1/ {gsub(/addr:/, "", $2); print $2; exit}')
    fi

    # Fallback
    if [ -z "$ip" ]; then
        ip="localhost"
    fi

    echo "$ip"
}

#-------------------------------------------------------------------------------
# Funções de Interação
#-------------------------------------------------------------------------------

ask_yes_no() {
    local prompt="$1"
    local default="${2:-y}"

    if [ "$NON_INTERACTIVE" = true ]; then
        [ "$default" = "y" ] && return 0 || return 1
    fi

    local yn_prompt
    if [ "$default" = "y" ]; then
        yn_prompt="[S/n]"
    else
        yn_prompt="[s/N]"
    fi

    while true; do
        read -p "$prompt $yn_prompt: " yn
        yn=${yn:-$default}
        case $yn in
            [Ss]* | [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Por favor, responda S (sim) ou N (não).";;
        esac
    done
}

ask_input() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [ "$NON_INTERACTIVE" = true ]; then
        eval "$var_name=\"$default\""
        return
    fi

    local input
    read -p "$prompt [$default]: " input
    input=${input:-$default}
    eval "$var_name=\"$input\""
}

ask_choice() {
    local prompt="$1"
    shift
    local options=("$@")

    if [ "$NON_INTERACTIVE" = true ]; then
        echo "1"
        return
    fi

    echo "$prompt"
    local i=1
    for opt in "${options[@]}"; do
        echo "  $i) $opt"
        ((i++))
    done

    local choice
    while true; do
        read -p "Escolha [1]: " choice
        choice=${choice:-1}
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#options[@]}" ]; then
            echo "$choice"
            return
        fi
        echo "Opção inválida. Digite um número entre 1 e ${#options[@]}."
    done
}

#-------------------------------------------------------------------------------
# Funções de Verificação
#-------------------------------------------------------------------------------

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_warning "Este script precisa de permissões de root para algumas operações."
        if ask_yes_no "Deseja continuar com sudo?"; then
            exec sudo "$0" "$@"
        else
            print_error "Instalação cancelada."
            exit 1
        fi
    fi
}

check_prerequisites() {
    print_step "1/11 - Verificando pré-requisitos"

    local missing=()

    # Docker
    if command -v docker &> /dev/null; then
        local docker_version=$(docker --version | sed 's/[^0-9.]*\([0-9.]*\).*/\1/' | head -1)
        print_success "Docker instalado (v$docker_version)"
    else
        missing+=("docker")
        print_error "Docker não encontrado"
    fi

    # Docker Compose
    if command -v docker-compose &> /dev/null; then
        local compose_version=$(docker-compose --version | sed 's/[^0-9.]*\([0-9.]*\).*/\1/' | head -1)
        print_success "Docker Compose instalado (v$compose_version)"
    elif docker compose version &> /dev/null; then
        print_success "Docker Compose (plugin) instalado"
        # Criar alias para compatibilidade
        alias docker-compose='docker compose'
    else
        missing+=("docker-compose")
        print_error "Docker Compose não encontrado"
    fi

    # Curl
    if command -v curl &> /dev/null; then
        print_success "Curl instalado"
    else
        missing+=("curl")
        print_error "Curl não encontrado"
    fi

    # Git (opcional)
    if command -v git &> /dev/null; then
        print_success "Git instalado (download via git clone)"
    else
        print_warning "Git não encontrado (será usado download via curl)"
    fi

    # Verificar permissão Docker
    if docker info &> /dev/null; then
        print_success "Permissão Docker OK"
    else
        print_error "Sem permissão para usar Docker"
        echo ""
        echo "Execute: sudo usermod -aG docker \$USER"
        echo "Depois faça logout e login novamente."
        exit 1
    fi

    # Verificar se há erros críticos
    if [ ${#missing[@]} -gt 0 ]; then
        echo ""
        print_error "Dependências faltando: ${missing[*]}"
        echo ""
        echo "Instale as dependências e execute novamente:"
        echo "  Ubuntu/Debian: sudo apt install ${missing[*]}"
        echo "  CentOS/RHEL:   sudo yum install ${missing[*]}"
        exit 1
    fi

    print_success "Todos os pré-requisitos atendidos!"
}

#-------------------------------------------------------------------------------
# Detecção do Home Assistant
#-------------------------------------------------------------------------------

detect_ha_container() {
    print_step "2/11 - Detectando Home Assistant"

    # Tentar encontrar container do HA
    local containers=$(docker ps --format "{{.Names}}" 2>/dev/null)

    for name in homeassistant home-assistant hass ha; do
        if echo "$containers" | grep -qi "^${name}$"; then
            HA_CONTAINER="$name"
            break
        fi
    done

    # Tentar por imagem
    if [ -z "$HA_CONTAINER" ]; then
        HA_CONTAINER=$(docker ps --filter "ancestor=homeassistant/home-assistant" --format "{{.Names}}" | head -1)
    fi

    if [ -z "$HA_CONTAINER" ]; then
        HA_CONTAINER=$(docker ps --filter "ancestor=ghcr.io/home-assistant/home-assistant" --format "{{.Names}}" | head -1)
    fi

    if [ -n "$HA_CONTAINER" ]; then
        print_success "Container Home Assistant encontrado: $HA_CONTAINER"

        # Encontrar diretório de configuração
        HA_CONFIG_DIR=$(docker inspect "$HA_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)

        if [ -n "$HA_CONFIG_DIR" ]; then
            print_success "Diretório de configuração: $HA_CONFIG_DIR"
        else
            print_warning "Não foi possível detectar o diretório de configuração"
        fi

        # Encontrar IP do container ou host
        HA_IP=$(docker inspect "$HA_CONTAINER" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null | tr -d '[:space:]')

        if [ -z "$HA_IP" ] || [ "$HA_IP" = "" ]; then
            # Container em host network, usar IP do host
            HA_IP=$(get_host_ip)
        fi
    else
        print_warning "Container do Home Assistant não detectado automaticamente"
        HA_IP=$(get_host_ip)
    fi

    # Validar se IP parece válido (formato básico)
    if echo "$HA_IP" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        print_success "IP detectado: $HA_IP"
    else
        # IP não é necessário para a integração (server-to-server)
        HA_IP="localhost"
    fi

    # Confirmar ou pedir informações
    if [ "$NON_INTERACTIVE" = false ]; then
        echo ""

        if [ -n "$HA_CONFIG_DIR" ]; then
            if ! ask_yes_no "Usar diretório detectado ($HA_CONFIG_DIR)?"; then
                ask_input "Digite o caminho do diretório de configuração do HA" "/home/$USER/homeassistant" HA_CONFIG_DIR
            fi
        else
            ask_input "Digite o caminho do diretório de configuração do HA" "/home/$USER/homeassistant" HA_CONFIG_DIR
        fi
    fi

    # Verificar se diretório existe
    if [ ! -d "$HA_CONFIG_DIR" ]; then
        print_error "Diretório não encontrado: $HA_CONFIG_DIR"
        if ask_yes_no "Deseja criar o diretório?"; then
            mkdir -p "$HA_CONFIG_DIR"
            print_success "Diretório criado: $HA_CONFIG_DIR"
        else
            print_error "Instalação cancelada."
            exit 1
        fi
    fi
}

#-------------------------------------------------------------------------------
# Configuração de Diretórios
#-------------------------------------------------------------------------------

setup_directories() {
    print_step "3/11 - Configurando diretórios"

    # Calcular diretório padrão baseado no HA_CONFIG_DIR
    # Se HA config está em /home/joao/docker-volumes/home-assistant/config
    # Usar /home/joao/docker-volumes/intelbras-guardian
    local default_dir="$DEFAULT_INSTALL_DIR"
    if [ -n "$HA_CONFIG_DIR" ]; then
        # Pegar o diretório pai do pai (ex: /home/joao/docker-volumes)
        local parent_dir=$(dirname "$(dirname "$HA_CONFIG_DIR")")
        if [ -d "$parent_dir" ] && [ -w "$parent_dir" ]; then
            default_dir="${parent_dir}/intelbras-guardian"
        fi
    fi

    # Diretório de instalação (código + dados no mesmo lugar)
    ask_input "Diretório da API" "$default_dir" INSTALL_DIR

    # Dados ficam no mesmo diretório
    DATA_DIR="${INSTALL_DIR}/data"

    # Porta da API
    ask_input "Porta da API" "$DEFAULT_API_PORT" API_PORT

    # Verificar se porta está em uso
    if ss -tuln 2>/dev/null | grep -q ":$API_PORT " || netstat -tuln 2>/dev/null | grep -q ":$API_PORT "; then
        print_warning "Porta $API_PORT parece estar em uso!"
        if ! ask_yes_no "Deseja continuar mesmo assim?"; then
            ask_input "Digite uma porta alternativa" "8001" API_PORT
        fi
    fi

    # Criar diretórios
    echo ""
    print_info "Criando diretórios..."

    mkdir -p "$INSTALL_DIR"
    mkdir -p "$DATA_DIR"
    chmod 755 "$DATA_DIR"
    print_success "Criado: $INSTALL_DIR"
}

#-------------------------------------------------------------------------------
# Download dos Arquivos
#-------------------------------------------------------------------------------

download_files() {
    print_step "4/11 - Baixando arquivos"

    cd "$INSTALL_DIR"

    # Verificar se já existe instalação
    if [ -f "$INSTALL_DIR/docker/Dockerfile" ]; then
        print_warning "Instalação existente detectada em $INSTALL_DIR"
        if ask_yes_no "Deseja sobrescrever?"; then
            rm -rf "$INSTALL_DIR"/*
        else
            print_info "Mantendo arquivos existentes"
            return
        fi
    fi

    # Tentar git clone primeiro
    if command -v git &> /dev/null; then
        print_info "Clonando repositório via git..."
        if git clone --depth 1 "$GITHUB_REPO.git" "$INSTALL_DIR/temp" 2>/dev/null; then
            mv "$INSTALL_DIR/temp"/* "$INSTALL_DIR/" 2>/dev/null || true
            mv "$INSTALL_DIR/temp"/.* "$INSTALL_DIR/" 2>/dev/null || true
            rm -rf "$INSTALL_DIR/temp"
            print_success "Download via git concluído!"
            return
        else
            print_warning "Falha no git clone, tentando download via curl..."
        fi
    fi

    # Fallback: download via curl
    print_info "Baixando arquivo zip..."
    curl -L "$GITHUB_ZIP" -o /tmp/guardian-api.zip

    print_info "Extraindo arquivos..."
    if command -v unzip &> /dev/null; then
        unzip -q /tmp/guardian-api.zip -d /tmp/
    else
        # Tentar com python
        python3 -c "import zipfile; zipfile.ZipFile('/tmp/guardian-api.zip').extractall('/tmp/')"
    fi

    mv /tmp/guardian-api-intelbras-main/* "$INSTALL_DIR/"
    rm -rf /tmp/guardian-api.zip /tmp/guardian-api-intelbras-main

    print_success "Download concluído!"
}

#-------------------------------------------------------------------------------
# Configuração do CORS
#-------------------------------------------------------------------------------

configure_cors() {
    # CORS não é necessário - HA backend se comunica diretamente com a API (server-to-server)
    # Mantemos '*' apenas para a Web UI de testes
    CORS_ORIGINS="*"
}

#-------------------------------------------------------------------------------
# Modo de Deploy
#-------------------------------------------------------------------------------

choose_deploy_mode() {
    print_step "5/11 - Escolha do modo de deploy"

    echo "Como deseja instalar a API?"
    echo ""
    local choice=$(ask_choice "" \
        "Standalone (docker-compose separado - Recomendado)" \
        "Integrar ao docker-compose existente do Home Assistant")

    case $choice in
        1) DEPLOY_MODE="standalone" ;;
        2) DEPLOY_MODE="integrated" ;;
    esac

    print_success "Modo selecionado: $DEPLOY_MODE"
}

#-------------------------------------------------------------------------------
# Geração do Docker Compose
#-------------------------------------------------------------------------------

generate_docker_compose() {
    print_step "6/11 - Gerando configuração Docker"

    if [ "$DEPLOY_MODE" = "standalone" ]; then
        # Criar docker-compose.override.yml com configurações personalizadas
        cat > "$INSTALL_DIR/docker-compose.override.yml" << EOF
version: '3.8'

services:
  fastapi:
    container_name: intelbras-guardian-api
    ports:
      - "${API_PORT}:8000"
    environment:
      - INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
      - INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
      - INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa
      - HOST=0.0.0.0
      - PORT=8000
      - DEBUG=false
      - LOG_LEVEL=INFO
      - HTTP_TIMEOUT=30
      - TOKEN_REFRESH_BUFFER=300
      - CORS_ORIGINS=${CORS_ORIGINS}
    volumes:
      - ${DATA_DIR}:/app/data
    restart: unless-stopped
EOF
        print_success "Criado: docker-compose.override.yml"

    else
        # Modo integrado - adicionar ao docker-compose existente
        print_warning "Modo integrado requer edição manual do docker-compose.yml do HA"
        echo ""
        echo "Adicione o seguinte serviço ao seu docker-compose.yml:"
        echo ""
        cat << EOF
  intelbras-guardian-api:
    build:
      context: ${INSTALL_DIR}
      dockerfile: docker/Dockerfile
    container_name: intelbras-guardian-api
    ports:
      - "${API_PORT}:8000"
    environment:
      - INTELBRAS_API_URL=https://api-guardian.intelbras.com.br:8443
      - INTELBRAS_OAUTH_URL=https://api.conta.intelbras.com/auth
      - INTELBRAS_CLIENT_ID=xHCEFEMoQnBcIHcw8ACqbU9aZaYa
      - CORS_ORIGINS=${CORS_ORIGINS}
      - LOG_LEVEL=INFO
    volumes:
      - ${DATA_DIR}:/app/data
    restart: unless-stopped
EOF
        echo ""
        if ! ask_yes_no "Você adicionou o serviço ao docker-compose.yml?"; then
            print_warning "Lembre-se de adicionar manualmente depois."
        fi
    fi
}

#-------------------------------------------------------------------------------
# Instalação da Integração HA
#-------------------------------------------------------------------------------

install_ha_integration() {
    print_step "7/11 - Instalando integração do Home Assistant"

    local custom_components="$HA_CONFIG_DIR/custom_components"
    local integration_src="$INSTALL_DIR/home_assistant/custom_components/intelbras_guardian"
    local integration_dst="$custom_components/intelbras_guardian"

    # Criar diretório custom_components se não existir
    if [ ! -d "$custom_components" ]; then
        mkdir -p "$custom_components"
        print_success "Criado diretório: $custom_components"
    fi

    # Verificar se já existe
    if [ -d "$integration_dst" ]; then
        print_warning "Integração já existe em $integration_dst"
        if ask_yes_no "Deseja sobrescrever?"; then
            rm -rf "$integration_dst"
        else
            print_info "Mantendo integração existente"
            return
        fi
    fi

    # Copiar arquivos
    cp -r "$integration_src" "$integration_dst"

    # Ajustar permissões se HA roda como outro usuário
    if [ -n "$HA_CONTAINER" ]; then
        # Tentar descobrir UID do container
        local ha_uid=$(docker inspect "$HA_CONTAINER" --format '{{.Config.User}}' 2>/dev/null | cut -d: -f1)
        if [ -n "$ha_uid" ] && [ "$ha_uid" != "" ]; then
            chown -R "$ha_uid" "$integration_dst" 2>/dev/null || true
        fi
    fi

    print_success "Integração instalada em: $integration_dst"

    # Listar arquivos instalados
    echo ""
    print_info "Arquivos instalados:"
    ls -la "$integration_dst"
}

#-------------------------------------------------------------------------------
# Iniciar Serviços
#-------------------------------------------------------------------------------

start_services() {
    print_step "8/11 - Iniciando serviços"

    cd "$INSTALL_DIR"

    print_info "Construindo imagem Docker..."
    if [ "$DEPLOY_MODE" = "standalone" ]; then
        docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml build
    else
        docker-compose build 2>/dev/null || docker build -t intelbras-guardian-api -f docker/Dockerfile .
    fi
    print_success "Imagem construída!"

    print_info "Iniciando container..."
    if [ "$DEPLOY_MODE" = "standalone" ]; then
        docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml up -d
    else
        print_warning "Para modo integrado, inicie via seu docker-compose do HA"
        return
    fi

    # Aguardar container ficar healthy
    print_info "Aguardando serviço iniciar..."
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if curl -s "http://localhost:${API_PORT}/api/v1/health" 2>/dev/null | grep -q "healthy"; then
            echo ""
            print_success "Serviço iniciado com sucesso!"
            return
        fi
        echo -n "."
        sleep 2
        ((attempt++))
    done

    echo ""
    print_error "Timeout aguardando serviço iniciar"
    print_info "Verifique os logs: docker logs intelbras-guardian-api"
}

#-------------------------------------------------------------------------------
# Verificação de Saúde
#-------------------------------------------------------------------------------

verify_installation() {
    print_step "9/11 - Verificando instalação"

    local errors=0

    # Verificar container
    if docker ps | grep -q "intelbras-guardian-api"; then
        print_success "Container rodando"
    else
        print_error "Container não está rodando"
        ((errors++))
    fi

    # Verificar endpoint health
    if curl -s "http://localhost:${API_PORT}/api/v1/health" 2>/dev/null | grep -q "healthy"; then
        print_success "API respondendo em http://localhost:${API_PORT}"
    else
        print_error "API não está respondendo"
        ((errors++))
    fi

    # Verificar integração HA
    if [ -f "$HA_CONFIG_DIR/custom_components/intelbras_guardian/manifest.json" ]; then
        print_success "Integração HA instalada"
    else
        print_error "Integração HA não encontrada"
        ((errors++))
    fi

    # Verificar diretório de dados
    if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
        print_success "Diretório de dados acessível"
    else
        print_warning "Diretório de dados pode ter problemas de permissão"
    fi

    if [ $errors -gt 0 ]; then
        print_warning "Instalação concluída com $errors erro(s)"
    else
        print_success "Todas as verificações passaram!"
    fi
}

#-------------------------------------------------------------------------------
# Configuração do Systemd
#-------------------------------------------------------------------------------

setup_systemd() {
    print_step "10/11 - Configuração de auto-início"

    if ! ask_yes_no "Deseja criar serviço systemd para iniciar automaticamente no boot?"; then
        print_info "Pulando configuração do systemd"
        return
    fi

    local service_file="/etc/systemd/system/intelbras-guardian.service"

    cat > "$service_file" << EOF
[Unit]
Description=Intelbras Guardian API
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml up -d
ExecStop=/usr/bin/docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable intelbras-guardian.service

    print_success "Serviço systemd criado e habilitado"
    print_info "Comandos disponíveis:"
    echo "  sudo systemctl start intelbras-guardian"
    echo "  sudo systemctl stop intelbras-guardian"
    echo "  sudo systemctl status intelbras-guardian"
}

#-------------------------------------------------------------------------------
# Resumo Final
#-------------------------------------------------------------------------------

print_summary() {
    print_step "11/11 - Instalação Concluída!"

    local host_ip=$(hostname -I | awk '{print $1}')

    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║           INSTALAÇÃO CONCLUÍDA COM SUCESSO!                      ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║                                                                  ║"
    echo "║  API FastAPI:                                                    ║"
    echo "║    • URL: http://${host_ip}:${API_PORT}                          "
    echo "║    • Swagger: http://${host_ip}:${API_PORT}/docs                 "
    echo "║    • Health: http://${host_ip}:${API_PORT}/api/v1/health         "
    echo "║                                                                  ║"
    echo "║  Arquivos instalados:                                            ║"
    echo "║    • API: ${INSTALL_DIR}                                         "
    echo "║    • Dados: ${DATA_DIR}                                          "
    echo "║    • Integração HA: ${HA_CONFIG_DIR}/custom_components/          "
    echo "║                                                                  ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  PRÓXIMOS PASSOS:                                                ║"
    echo "║                                                                  ║"
    echo "║  1. Reiniciar Home Assistant:                                    ║"
    if [ -n "$HA_CONTAINER" ]; then
    echo "║     docker restart ${HA_CONTAINER}                               "
    else
    echo "║     docker restart homeassistant                                 "
    fi
    echo "║                                                                  ║"
    echo "║  2. Adicionar integração no HA:                                  ║"
    echo "║     Configurações → Dispositivos e Serviços →                    ║"
    echo "║     Adicionar Integração → \"Intelbras Guardian\"                  ║"
    echo "║                                                                  ║"
    echo "║  3. Configurar:                                                  ║"
    echo "║     • Email: seu_email@intelbras.com                             ║"
    echo "║     • Senha: sua_senha                                           ║"
    echo "║     • Host FastAPI: ${host_ip}                                   "
    echo "║     • Porta: ${API_PORT}                                         "
    echo "║                                                                  ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  COMANDOS ÚTEIS:                                                 ║"
    echo "║                                                                  ║"
    echo "║  • Ver logs:                                                     ║"
    echo "║    docker logs -f intelbras-guardian-api                         ║"
    echo "║                                                                  ║"
    echo "║  • Reiniciar API:                                                ║"
    echo "║    docker restart intelbras-guardian-api                         ║"
    echo "║                                                                  ║"
    echo "║  • Parar:                                                        ║"
    echo "║    cd ${INSTALL_DIR} && docker-compose down                      "
    echo "║                                                                  ║"
    echo "║  • Atualizar:                                                    ║"
    echo "║    cd ${INSTALL_DIR} && git pull && \\                           "
    echo "║    docker-compose up -d --build                                  ║"
    echo "║                                                                  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

#-------------------------------------------------------------------------------
# Desinstalação
#-------------------------------------------------------------------------------

uninstall() {
    print_header
    print_step "Desinstalação"

    print_warning "Isso irá remover completamente a instalação do Intelbras Guardian."
    echo ""

    if ! ask_yes_no "Tem certeza que deseja continuar?" "n"; then
        print_info "Desinstalação cancelada."
        exit 0
    fi

    # Parar container
    print_info "Parando container..."
    docker stop intelbras-guardian-api 2>/dev/null || true
    docker rm intelbras-guardian-api 2>/dev/null || true
    print_success "Container removido"

    # Remover imagem
    if ask_yes_no "Remover imagem Docker?"; then
        docker rmi intelbras-guardian-api 2>/dev/null || true
        print_success "Imagem removida"
    fi

    # Perguntar diretório de instalação
    ask_input "Diretório de instalação" "$DEFAULT_INSTALL_DIR" INSTALL_DIR

    # Remover arquivos
    if [ -d "$INSTALL_DIR" ]; then
        if ask_yes_no "Remover diretório $INSTALL_DIR?"; then
            rm -rf "$INSTALL_DIR"
            print_success "Diretório de instalação removido"
        fi
    fi

    # Remover dados
    ask_input "Diretório de dados" "$DEFAULT_DATA_DIR" DATA_DIR
    if [ -d "$DATA_DIR" ]; then
        if ask_yes_no "Remover dados persistentes em $DATA_DIR?" "n"; then
            rm -rf "$DATA_DIR"
            print_success "Dados removidos"
        else
            print_info "Dados mantidos em $DATA_DIR"
        fi
    fi

    # Remover integração HA
    ask_input "Diretório config do HA" "/home/$USER/homeassistant" HA_CONFIG_DIR
    local integration_dir="$HA_CONFIG_DIR/custom_components/intelbras_guardian"
    if [ -d "$integration_dir" ]; then
        if ask_yes_no "Remover integração do Home Assistant?"; then
            rm -rf "$integration_dir"
            print_success "Integração removida"
            print_warning "Reinicie o Home Assistant para aplicar"
        fi
    fi

    # Remover serviço systemd
    if [ -f "/etc/systemd/system/intelbras-guardian.service" ]; then
        if ask_yes_no "Remover serviço systemd?"; then
            systemctl stop intelbras-guardian 2>/dev/null || true
            systemctl disable intelbras-guardian 2>/dev/null || true
            rm -f /etc/systemd/system/intelbras-guardian.service
            systemctl daemon-reload
            print_success "Serviço systemd removido"
        fi
    fi

    echo ""
    print_success "Desinstalação concluída!"
}

#-------------------------------------------------------------------------------
# Atualização
#-------------------------------------------------------------------------------

update() {
    print_header
    print_step "Atualização"

    ask_input "Diretório de instalação" "$DEFAULT_INSTALL_DIR" INSTALL_DIR

    if [ ! -d "$INSTALL_DIR" ]; then
        print_error "Instalação não encontrada em $INSTALL_DIR"
        exit 1
    fi

    cd "$INSTALL_DIR"

    # Atualizar via git
    if [ -d ".git" ]; then
        print_info "Atualizando via git..."
        git pull
        print_success "Código atualizado"
    else
        print_warning "Não é um repositório git. Baixando nova versão..."
        # Backup de arquivos de configuração
        cp docker-compose.override.yml /tmp/ 2>/dev/null || true

        # Download novo
        curl -L "$GITHUB_ZIP" -o /tmp/guardian-api.zip
        unzip -o /tmp/guardian-api.zip -d /tmp/
        cp -r /tmp/guardian-api-intelbras-main/* "$INSTALL_DIR/"

        # Restaurar configuração
        cp /tmp/docker-compose.override.yml "$INSTALL_DIR/" 2>/dev/null || true

        rm -rf /tmp/guardian-api.zip /tmp/guardian-api-intelbras-main
        print_success "Código atualizado"
    fi

    # Rebuild container
    print_info "Reconstruindo container..."
    docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml build
    docker-compose -f docker/docker-compose.yml -f docker-compose.override.yml up -d
    print_success "Container atualizado"

    # Atualizar integração HA
    ask_input "Diretório config do HA" "/home/$USER/homeassistant" HA_CONFIG_DIR
    local integration_dst="$HA_CONFIG_DIR/custom_components/intelbras_guardian"

    if [ -d "$integration_dst" ]; then
        print_info "Atualizando integração do Home Assistant..."
        rm -rf "$integration_dst"
        cp -r "$INSTALL_DIR/home_assistant/custom_components/intelbras_guardian" "$integration_dst"
        print_success "Integração atualizada"
        print_warning "Reinicie o Home Assistant para aplicar: docker restart homeassistant"
    fi

    echo ""
    print_success "Atualização concluída!"
}

#-------------------------------------------------------------------------------
# Ajuda
#-------------------------------------------------------------------------------

show_help() {
    echo "Uso: $0 [opções]"
    echo ""
    echo "Script de instalação automatizada do Intelbras Guardian para Home Assistant"
    echo ""
    echo "Opções:"
    echo "  -h, --help              Mostra esta ajuda"
    echo "  -y, --yes               Modo não-interativo (aceita todos os padrões)"
    echo "  -d, --dir DIR           Define diretório de instalação"
    echo "  -c, --config DIR        Define diretório de configuração do HA"
    echo "  --uninstall             Desinstalar completamente"
    echo "  --update                Atualizar instalação existente"
    echo ""
    echo "Exemplos:"
    echo "  $0                      Instalação interativa"
    echo "  $0 -y                   Instalação com padrões"
    echo "  $0 --uninstall          Remover instalação"
    echo "  $0 --update             Atualizar para última versão"
    echo ""
    echo "Mais informações: $GITHUB_REPO"
}

#-------------------------------------------------------------------------------
# Parser de Argumentos
#-------------------------------------------------------------------------------

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -y|--yes)
                NON_INTERACTIVE=true
                shift
                ;;
            -d|--dir)
                INSTALL_DIR="$2"
                shift 2
                ;;
            -c|--config)
                HA_CONFIG_DIR="$2"
                shift 2
                ;;
            --uninstall)
                uninstall
                exit 0
                ;;
            --update)
                update
                exit 0
                ;;
            *)
                print_error "Opção desconhecida: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

#-------------------------------------------------------------------------------
# Função Principal
#-------------------------------------------------------------------------------

main() {
    parse_args "$@"

    print_header

    # Verificar root para algumas operações
    if [ "$EUID" -ne 0 ]; then
        print_warning "Algumas operações podem requerer sudo."
        print_info "Se encontrar erros de permissão, execute: sudo $0 $*"
        echo ""
    fi

    check_prerequisites
    detect_ha_container
    setup_directories
    download_files
    configure_cors
    choose_deploy_mode
    generate_docker_compose
    install_ha_integration
    start_services
    verify_installation
    setup_systemd
    print_summary
}

#-------------------------------------------------------------------------------
# Ponto de Entrada
#-------------------------------------------------------------------------------

main "$@"
