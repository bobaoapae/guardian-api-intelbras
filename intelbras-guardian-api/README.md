# Intelbras Guardian API Add-on

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5.svg)
![License](https://img.shields.io/github/license/bobaoapae/guardian-api-intelbras)

Middleware de API para integração de sistemas de alarme Intelbras Guardian com Home Assistant.

## Sobre

Este add-on fornece um middleware FastAPI que se comunica com centrais de alarme Intelbras Guardian usando:
- Autenticação **OAuth 2.0** com Intelbras Cloud
- **Protocolo ISECNet** para comunicação direta com centrais de alarme

## Funcionalidades

- Status do alarme em tempo real (armar/desarmar, disparado, zonas)
- Armar/Desarmar partições (modos ausente e em casa)
- Monitoramento de zonas com nomes amigáveis personalizados
- Controle de eletrificador (cerca elétrica)
- Histórico de eventos
- Web UI para configuração e testes

## Instalação

1. Adicione este repositório à Loja de Add-ons do Home Assistant:

   [![Adicionar Repositório](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbobaoapae%2Fguardian-api-intelbras)

   Ou manualmente: **Configurações** → **Add-ons** → **Loja de Add-ons** → **⋮** → **Repositórios** → Adicione `https://github.com/bobaoapae/guardian-api-intelbras`

2. Encontre "Intelbras Guardian API" na loja de add-ons e clique em **Instalar**

3. Inicie o add-on

4. Abra a Web UI em `http://[SEU_IP_HA]:8000`

## Configuração

```yaml
log_level: info
```

### Opção: `log_level`

Nível de log do add-on. Opções: `trace`, `debug`, `info`, `warning`, `error`, `critical`

## Uso

### 1. Acessar Web UI

Após iniciar o add-on, acesse `http://[SEU_IP_HA]:8000` para:
- Fazer login com sua conta Intelbras
- Ver seus dispositivos
- Salvar senhas dos dispositivos para comunicação ISECNet
- Configurar nomes amigáveis das zonas
- Testar comandos de armar/desarmar

### 2. Instalar Integração do Home Assistant

O add-on fornece apenas o middleware de API. Você também precisa da integração do Home Assistant:

1. Copie `home_assistant/custom_components/intelbras_guardian` para a pasta `custom_components` do seu Home Assistant

2. Reinicie o Home Assistant

3. Adicione a integração: **Configurações** → **Dispositivos e Serviços** → **Adicionar Integração** → "Intelbras Guardian"

4. Configure:
   - **Email**: Email da sua conta Intelbras
   - **Senha**: Senha da sua conta Intelbras
   - **Host FastAPI**: `localhost` ou o IP do seu HA
   - **Porta FastAPI**: `8000`

### 3. Configurar Senha do Dispositivo

Para status em tempo real via protocolo ISECNet, salve a senha do seu dispositivo:

**Opção A - Via Home Assistant:**
- Configurações → Dispositivos e Serviços → Intelbras Guardian → Configurar → Configurar Senha do Dispositivo

**Opção B - Via Web UI:**
- Acesse `http://[SEU_IP_HA]:8000` → Login → Clique em "Salvar Senha" no seu dispositivo

## Rede

O add-on expõe a porta `8000` para a API e Web UI.

## Suporte

- [GitHub Issues](https://github.com/bobaoapae/guardian-api-intelbras/issues)
- [Documentação](https://github.com/bobaoapae/guardian-api-intelbras)

## Aviso Legal

Este add-on NÃO é afiliado à Intelbras. Use por sua conta e risco.
