# test_cache_logic.py
import json

def test_caching_logic():
    # Эмуляция данных
    tool_calls = [{"id": "call_123", "function": {"name": "read_file", "arguments": "{}"}}]
    orchestrator_model = "claude-3-5-sonnet-20240620"
    tool_result = "File content here..."
    
    # === ТА ЖЕ ЛОГИКА, ЧТО МЫ ПИСАЛИ ===
    is_claude_model = "claude" in orchestrator_model.lower()
    tool_results = []
    
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        tc_id = tc["id"]
        
        is_heavy_tool = func_name in ["read_file", "read_code_chunk"]
        use_caching = is_claude_model and is_heavy_tool
        
        if use_caching:
            tool_results.append({
                "tool_call_id": tc_id,
                "role": "tool",
                "name": func_name,
                "content": [
                    {
                        "type": "text", 
                        "text": tool_result,
                        "cache_control": {"type": "ephemeral"} 
                    }
                ]
            })
        else:
             tool_results.append({"role": "tool", "content": tool_result})
             
    # ПРОВЕРКА
    print("Result structure:")
    print(json.dumps(tool_results, indent=2))
    
    # Assert
    assert "cache_control" in tool_results[0]["content"][0], "Cache control missing!"
    print("\n✅ TEST PASSED: Cache control is present.")

if __name__ == "__main__":
    test_caching_logic()
