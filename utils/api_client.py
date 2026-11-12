import time
import requests
from flask import current_app

def safe_api_request(method, url, headers=None, json_data=None, max_retries=3, backoff_factor=2):
    """
    Executa requisições HTTP para APIs de e-commerce com política de retries
    e tratamento rigoroso de falhas.
    """
    retries = 0
    while retries < max_retries:
        try:
            response = requests.request(method, url, headers=headers, json=json_data, timeout=30)
            
            # Se a requisição foi bem-sucedida, retorna o JSON ou o objeto de resposta
            if response.status_code in [200, 201, 204]:
                return response.json() if response.content else {}
                
            # Se atingir rate limit, aguarda antes de tentar novamente
            elif response.status_code == 429:
                wait_time = backoff_factor ** retries
                time.sleep(wait_time)
                retries += 1
                continue
                
            # Outros erros de cliente ou servidor
            else:
                current_app.logger.error(f"Erro na API {url}: Status {response.status_code} - {response.text}")
                return {"error": True, "status_code": response.status_code, "message": response.text}
                
        except requests.exceptions.RequestException as e:
            current_app.logger.warning(f"Tentativa {retries + 1} falhou para {url} devido a erro de rede: {e}")
            retries += 1
            if retries == max_retries:
                return {"error": True, "message": str(e)}
            time.sleep(backoff_factor ** retries)
            
    return {"error": True, "message": "Máximo de tentativas excedido."}
