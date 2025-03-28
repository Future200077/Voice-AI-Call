import requests
import json
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse  

def add_or_update_query_params(url, additional_params):  
    # Parse the original URL  
    parsed_url = urlparse(url)  
    
    # Parse the existing query parameters from the URL  
    existing_params = parse_qs(parsed_url.query)  
    
    # Update/add the existing parameters with additional parameters  
    for key, value in additional_params.items():  
        existing_params[key] = [value]  
    
    # Encode updated parameters  
    encoded_query_params = urlencode(existing_params, doseq=True)  
    
    # Construct a new URL with the updated query parameters  
    new_url = urlunparse((  
        parsed_url.scheme,  
        parsed_url.netloc,  
        parsed_url.path,  
        parsed_url.params,  
        encoded_query_params,  
        parsed_url.fragment  
    ))  
    
    return new_url  

def separate_args(args, path_params_keys, query_params_keys, body_params_keys):  

    path_params = {}  
    query_params = {}  
    body_params = {}  
    if path_params_keys is None:  
        path_params_keys = []  
    if query_params_keys is None:  
        query_params_keys = []  
    if body_params_keys is None:  
        body_params_keys = []  

    # Distribute the args among the dictionaries  
    for key, value in args.items():  
        if key in path_params_keys:  
            path_params[key] = value  
        elif key in query_params_keys:  
            query_params[key] = value  
        elif key in body_params_keys:  
            body_params[key] = value  

    return path_params, query_params, body_params

async def webhook(context, args):
    try:
        metadata = context.function_metadata
        http_method = metadata["httpMethod"]
        url = metadata["url"]
        headers = metadata["headers"]
        path_param_keys = metadata["pathParameters"]
        query_param_keys = metadata["queryParameters"]
        body_param_keys = metadata["bodyParameters"]

        path_params, query_params, body_params = separate_args(args, path_param_keys, query_param_keys, body_param_keys)  
            
        if path_params != {}:
            url = url.format(**path_params)  
        if query_params != {}:
            url = add_or_update_query_params(url, query_params)
        
        if http_method == "POST":
            response = requests.post(url, json=body_params, headers=headers)
        
        if http_method == "GET":
            response = requests.get(url, headers=headers)

        if http_method == "PATCH":
            response = requests.patch(url, json=body_params, headers=headers)
        
        if http_method == "DELETE":
            response = requests.delete(url, headers=headers)
        status_code = response.status_code  
        print("Status Code:", status_code)  

        # Always check the status code first to ensure the request was successful  
        if response.ok:  # Equivalent to status_code == 200  
            try:  
                response_data = json.dumps(response.json()  )
                print("Response JSON:", response_data)  
            except ValueError:  # If response is not JSON, you might get a ValueError  
                # If JSON parsing fails, fall back to plain text  
                response_data = response.text
                print("Response Text:", response.text)
            return response_data
        else:
            print("Request Failed:", response.text)
            return "Request Failed"
    except Exception as e:
        print(f"Error in webhook: {str(e)}")
        return "Error occurred to get information"