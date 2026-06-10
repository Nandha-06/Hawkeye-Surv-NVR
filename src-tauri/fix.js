const fs = require('fs');
const path = require('path');

const routesDir = 'src/server/routes';

function fixFile(filepath) {
    let content = fs.readFileSync(filepath, 'utf8');
    
    // Add StatusCode import
    if (!content.includes('use axum::http::StatusCode;')) {
        content = content.replace('use axum::Json;', 'use axum::Json;\nuse axum::http::StatusCode;');
    }
    
    // Replace standard return patterns
    // We match from Json(serde_json::json!(...)) up to the closing brace/paren.
    // Instead of complex regex, we just replace all "Json(" -> "Json(" and add .into_response()
    // but wait, we need to add the StatusCode!
    
    // A simpler way: just string replace the specific lines.
    content = content.split('\n').map(line => {
        if (line.includes('Json(serde_json::json!({ "error"')) {
            return line.replace(/Json\(serde_json::json!\((.*)\)\)/, '(axum::http::StatusCode::BAD_REQUEST, axum::Json(serde_json::json!($1))).into_response()');
        } else if (line.includes('Json(serde_json::json!({ "success": false')) {
            return line.replace(/Json\(serde_json::json!\((.*)\)\)/, '(axum::http::StatusCode::BAD_REQUEST, axum::Json(serde_json::json!($1))).into_response()');
        } else if (line.includes('Json(')) {
            // Need to append .into_response() to the success ones if they are part of a match that now has different types.
            // Actually, if we just append .into_response() to ALL Json(...) returns, it will unify the types!
            // But we must be careful not to append it twice.
        }
        return line;
    }).join('\n');
    
    // Since doing it line-by-line is hard, let's just use regex.
    // Replace Json(serde_json::json!({ "error": ... })) 
    content = content.replace(/Json\(serde_json::json!\(\{\s*"error":\s*(.*?)\}\)\)/g, 
        '(axum::http::StatusCode::BAD_REQUEST, axum::Json(serde_json::json!({ "error": $1 }))).into_response()');
        
    content = content.replace(/Json\(serde_json::json!\(\{\s*"success":\s*false,\s*"error":\s*(.*?)\}\)\)/g, 
        '(axum::http::StatusCode::BAD_REQUEST, axum::Json(serde_json::json!({ "success": false, "error": $1 }))).into_response()');

    // Replace success ones to add into_response
    content = content.replace(/Json\(serde_json::json!\(\{\s*"success":\s*true(.*?)\}\)\)/g, 
        'axum::Json(serde_json::json!({ "success": true$1 })).into_response()');
        
    content = content.replace(/Json\(serde_json::json!\(\[\]\)\)/g, 
        'axum::Json(serde_json::json!([])).into_response()');

    // Add .into_response() to Json(json) and Json(list)
    content = content.replace(/Json\(json\)/g, 'axum::Json(json).into_response()');
    content = content.replace(/Json\(list\)/g, 'axum::Json(list).into_response()');

    fs.writeFileSync(filepath, content, 'utf8');
}

fs.readdirSync(routesDir).forEach(file => {
    if (file.endsWith('.rs')) {
        fixFile(path.join(routesDir, file));
    }
});
