import os
import re

routes_dir = 'src/server/routes'

for filename in os.listdir(routes_dir):
    if not filename.endswith('.rs'): continue
    filepath = os.path.join(routes_dir, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Add StatusCode import if not present
    if 'use axum::http::StatusCode;' not in content and 'use axum::http::{' not in content:
        content = content.replace('use axum::Json;', 'use axum::Json;\nuse axum::http::StatusCode;')
    
    # 2. Replace Json({ 'error': ... }) with (StatusCode::BAD_REQUEST, Json(...)).into_response()
    def replacer_error(m):
        inner = m.group(1)
        if '"error"' in inner or '"success": false' in inner:
            return f'(axum::http::StatusCode::BAD_REQUEST, axum::Json({inner})).into_response()'
        return f'axum::Json({inner}).into_response()'

    content = re.sub(r'Json\((serde_json::json!.*?\})\)', replacer_error, content, flags=re.DOTALL)
    
    # 3. Handle success cases like Json(list)
    content = re.sub(r'(?<!axum::)Json\(([a-zA-Z0-9_]+)\)', r'axum::Json(\1).into_response()', content)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
print('Done!')
