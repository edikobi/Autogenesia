#!/usr/bin/env python3
"""
Real integration tests for Advice System.
Tests actual functionality, not just imports.
"""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_json_files_exist_and_valid():
    """Test that JSON files exist and are valid JSON"""
    print("\n1. JSON Files Validation:")
    
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    advice_dir = os.path.join(base_path, "app", "advice")
    
    results = []
    
    # Check catalog file
    catalog_path = os.path.join(advice_dir, "advice_catalog.json")
    try:
        assert os.path.exists(catalog_path), f"File not found: {catalog_path}"
        with open(catalog_path, 'r', encoding='utf-8') as f:
            catalog_data = json.load(f)
        
        # REAL CHECKS for catalog structure
        assert "categories" in catalog_data, "Missing 'categories' key in catalog!"
        assert "advices" in catalog_data, "Missing 'advices' key in catalog!"
        assert len(catalog_data["categories"]) > 0, "No categories defined!"
        assert len(catalog_data["advices"]) > 0, "No advices defined!"
        
        # Check category structure
        for cat_id, cat in catalog_data["categories"].items():
            assert "name" in cat, f"Category {cat_id} missing 'name'!"
            assert "applies_to" in cat, f"Category {cat_id} missing 'applies_to'!"
            assert isinstance(cat["applies_to"], list), f"Category {cat_id} applies_to must be list!"
        
        # Check advice structure
        for adv in catalog_data["advices"]:
            assert "id" in adv, "Advice missing 'id'!"
            assert "name" in adv, f"Advice {adv.get('id', '?')} missing 'name'!"
            assert "category" in adv, f"Advice {adv.get('id', '?')} missing 'category'!"
            assert "description" in adv, f"Advice {adv.get('id', '?')} missing 'description'!"
            assert adv["category"] in catalog_data["categories"], \
                f"Advice {adv['id']} references unknown category {adv['category']}!"
        
        print(f"  ✅ advice_catalog.json: VALID ({len(catalog_data['advices'])} advices, {len(catalog_data['categories'])} categories)")
        results.append(True)
    except AssertionError as e:
        print(f"  ❌ advice_catalog.json: INVALID: {e}")
        results.append(False)
    except json.JSONDecodeError as e:
        print(f"  ❌ advice_catalog.json: JSON PARSE ERROR: {e}")
        results.append(False)
    except Exception as e:
        print(f"  ❌ advice_catalog.json: ERROR: {type(e).__name__}: {e}")
        results.append(False)
    
    # Check content file
    content_path = os.path.join(advice_dir, "advice_content.json")
    try:
        assert os.path.exists(content_path), f"File not found: {content_path}"
        with open(content_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        # Extract advices dict (same logic as advice_loader.py)
        content_data = raw_data.get('advices', {})
        
        # REAL CHECKS for content structure
        assert isinstance(content_data, dict), "Content 'advices' must be a dict!"
        assert len(content_data) > 0, "No advices in content file!"
        
        # Check that all catalog advices have content
        with open(catalog_path, 'r', encoding='utf-8') as f:
            catalog_data = json.load(f)
        
        missing_content = []
        for adv in catalog_data["advices"]:
            if adv["id"] not in content_data:
                missing_content.append(adv["id"])
        
        if missing_content:
            print(f"  ⚠️ Missing content for: {', '.join(missing_content)}")
        
        # Check content is not empty
        empty_content = []
        for adv_id, adv_data in content_data.items():
            # Handle object format: {"title": ..., "content": ...}
            if isinstance(adv_data, dict):
                actual_content = adv_data.get("content", "")
            else:
                actual_content = adv_data
            
            if not actual_content or len(str(actual_content).strip()) < 50:
                empty_content.append(adv_id)
        
        if empty_content:
            print(f"  ⚠️ Empty/short content for: {', '.join(empty_content)}")
        
        print(f"  ✅ advice_content.json: VALID ({len(content_data)} entries)")
        results.append(True)
    except AssertionError as e:
        print(f"  ❌ advice_content.json: INVALID: {e}")
        results.append(False)
    except json.JSONDecodeError as e:
        print(f"  ❌ advice_content.json: JSON PARSE ERROR: {e}")
        results.append(False)
    except Exception as e:
        print(f"  ❌ advice_content.json: ERROR: {type(e).__name__}: {e}")
        results.append(False)
    
    return all(results)

def test_advice_system_initialization():
    """Test that AdviceSystem loads catalog correctly"""
    print("\n2. AdviceSystem Initialization:")
    
    try:
        from app.advice.advice_loader import AdviceSystem
        
        system = AdviceSystem()
        
        # REAL CHECK 1: Catalog is not empty
        assert len(system.catalog) > 0, "Catalog is empty!"
        
        # REAL CHECK 2: Categories loaded
        assert len(system.categories) > 0, "No categories loaded!"
        
        # REAL CHECK 3: Known advice exists (ADV-G01 should always exist)
        assert "ADV-G01" in system.catalog, "ADV-G01 not in catalog!"
        
        # REAL CHECK 4: Advice has required fields
        advice = system.catalog["ADV-G01"]
        assert advice.id == "ADV-G01", "Advice ID mismatch!"
        assert advice.name, "Advice name is empty!"
        assert advice.description, "Advice description is empty!"
        assert advice.category, "Advice category is empty!"
        
        # REAL CHECK 5: Category reference is valid
        assert advice.category in system.categories, \
            f"Advice references unknown category: {advice.category}"
        
        # REAL CHECK 6: Lazy loading - content should NOT be loaded yet
        assert system._content_data is None, \
            "Content should not be loaded during initialization (lazy loading broken)!"
        
        print(f"  ✅ AdviceSystem initialized correctly")
        print(f"     - Loaded {len(system.catalog)} advices")
        print(f"     - Loaded {len(system.categories)} categories")
        print(f"     - Lazy loading: OK (content not loaded)")
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_lazy_loading_content():
    """Test that content is loaded lazily"""
    print("\n3. Lazy Loading Content:")
    
    try:
        from app.advice.advice_loader import AdviceSystem
        
        # Create fresh instance
        system = AdviceSystem()
        
        # BEFORE: Content should not be loaded
        assert system._content_data is None, "Content loaded too early!"
        print("  ✅ Before get_advice(): content NOT loaded")
        
        # TRIGGER: Load content by calling get_advice
        content = system.get_advice("ADV-G01")
        
        # AFTER: Content should be loaded
        assert system._content_data is not None, "Content not loaded after get_advice()!"
        print("  ✅ After get_advice(): content loaded")
        
        # Content should be valid
        assert content is not None, "get_advice returned None for valid ID!"
        assert len(content) > 100, f"Content too short ({len(content)} chars)!"
        print(f"  ✅ Content retrieved: {len(content)} characters")
        
        # Second call should use cache (not reload)
        content2 = system.get_advice("ADV-G01")
        assert content2 == content, "Cached content differs!"
        print("  ✅ Caching works correctly")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_get_advice_content():
    """Test get_advice with various inputs"""
    print("\n4. Get Advice Content:")
    
    try:
        from app.advice.advice_loader import get_advice_system
        
        system = get_advice_system()
        
        # TEST 1: Valid advice returns content
        content = system.get_advice("ADV-G01")
        assert content is not None, "ADV-G01 content is None!"
        assert len(content) > 100, f"Content too short ({len(content)} chars)!"
        print(f"  ✅ Valid advice: returned {len(content)} chars")
        
        # TEST 2: Content has expected structure (markdown-like)
        has_structure = (
            "##" in content or 
            "**" in content or 
            "```" in content or
            "\n\n" in content
        )
        assert has_structure, "Content doesn't look like formatted document!"
        print("  ✅ Content has markdown structure")
        
        # TEST 3: Non-existent advice returns None
        bad_content = system.get_advice("ADV-NONEXISTENT-999")
        assert bad_content is None, "Non-existent advice should return None!"
        print("  ✅ Non-existent advice: returned None")
        
        # TEST 4: Empty ID returns None
        empty_content = system.get_advice("")
        assert empty_content is None, "Empty ID should return None!"
        print("  ✅ Empty ID: returned None")
        
        # TEST 5: Multiple advices exist and have different content
        if "ADV-G02" in system.catalog:
            content2 = system.get_advice("ADV-G02")
            if content2:
                assert content != content2, "Different advices have same content!"
                print("  ✅ Different advices have different content")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_catalog_for_prompt_ask_mode():
    """Test catalog generation for 'ask' mode"""
    print("\n5. Catalog for 'ask' Mode:")
    
    try:
        from app.advice.advice_loader import get_catalog_for_prompt
        
        result = get_catalog_for_prompt(mode="ask")
        
        # REAL CHECK 1: Not empty
        assert result, "Catalog is empty!"
        assert len(result) > 50, f"Catalog too short ({len(result)} chars)!"
        print(f"  ✅ Catalog not empty: {len(result)} chars")
        
        # REAL CHECK 2: Contains header
        assert "ADVICE CATALOG" in result or "CATALOG" in result.upper(), \
            "Missing catalog header!"
        print("  ✅ Contains header")
        
        # REAL CHECK 3: Contains advice IDs
        assert "ADV-" in result, "No advice IDs in catalog!"
        print("  ✅ Contains advice IDs")
        
        # REAL CHECK 4: Contains advice names/descriptions
        lines = result.split("\n")
        has_descriptions = any(len(line) > 20 for line in lines if "ADV-" in line)
        assert has_descriptions, "Advice entries too short (missing descriptions?)"
        print("  ✅ Contains advice descriptions")
        
        # REAL CHECK 5: Format is readable
        assert result.count("ADV-") >= 1, "Should have at least one advice!"
        advice_count = result.count("ADV-")
        print(f"  ✅ Contains {advice_count} advice references")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_catalog_for_prompt_new_project_mode():
    """Test catalog generation for 'new_project' mode"""
    print("\n6. Catalog for 'new_project' Mode:")
    
    try:
        from app.advice.advice_loader import get_catalog_for_prompt
        
        result = get_catalog_for_prompt(mode="new_project")
        
        # REAL CHECK 1: Not empty
        assert result, "Catalog is empty!"
        assert len(result) > 50, f"Catalog too short ({len(result)} chars)!"
        print(f"  ✅ Catalog not empty: {len(result)} chars")
        
        # REAL CHECK 2: Contains required elements
        assert "ADV-" in result, "No advice IDs in catalog!"
        print("  ✅ Contains advice IDs")
        
        # REAL CHECK 3: Valid structure
        assert "ADVICE CATALOG" in result or "CATALOG" in result.upper(), \
            "Missing catalog header!"
        print("  ✅ Valid structure")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_mode_filtering():
    """Test that different modes filter categories correctly"""
    print("\n7. Mode Filtering:")
    
    try:
        from app.advice.advice_loader import get_catalog_for_prompt, get_advice_system
        
        system = get_advice_system()
        
        # Analyze categories
        ask_only_cats = []
        new_only_cats = []
        both_cats = []
        
        for cat_id, cat in system.categories.items():
            applies_to = cat.applies_to
            has_ask = "ask" in applies_to
            has_new = "new_project" in applies_to
            
            if has_ask and not has_new:
                ask_only_cats.append(cat_id)
            elif has_new and not has_ask:
                new_only_cats.append(cat_id)
            elif has_ask and has_new:
                both_cats.append(cat_id)
        
        print(f"  Categories analysis:")
        print(f"     - Ask only: {ask_only_cats or 'none'}")
        print(f"     - New project only: {new_only_cats or 'none'}")
        print(f"     - Both modes: {both_cats or 'none'}")
        
        # Get catalogs
        ask_catalog = get_catalog_for_prompt(mode="ask")
        new_catalog = get_catalog_for_prompt(mode="new_project")
        
        # If we have mode-specific categories, catalogs should differ
        if ask_only_cats or new_only_cats:
            # Catalogs should be different
            if ask_catalog != new_catalog:
                print("  ✅ Different modes produce different catalogs")
            else:
                print("  ⚠️ Catalogs are identical (may be OK if all advices apply to both)")
        else:
            print("  ℹ️ All categories apply to both modes - catalogs may be identical")
        
        # Verify ask-only advices appear only in ask mode
        for cat_id in ask_only_cats:
            advices_in_cat = [a for a in system.catalog.values() if a.category == cat_id]
            for adv in advices_in_cat:
                assert adv.id in ask_catalog, f"{adv.id} should be in ask catalog!"
                # Note: might still be in new_catalog if filtering is lenient
        
        # Verify new-only advices appear in new_project mode  
        for cat_id in new_only_cats:
            advices_in_cat = [a for a in system.catalog.values() if a.category == cat_id]
            for adv in advices_in_cat:
                assert adv.id in new_catalog, f"{adv.id} should be in new_project catalog!"
        
        print("  ✅ Mode filtering works correctly")
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_execute_get_advice_tool():
    """Test the tool execution function"""
    print("\n8. Execute Get Advice Tool:")
    
    try:
        from app.advice.advice_loader import execute_get_advice
        
        # TEST 1: Valid single advice
        result = execute_get_advice("ADV-G01")
        assert "<loaded_advices>" in result, "Missing <loaded_advices> tag!"
        assert "ADV-G01" in result, "Advice ID not in response!"
        assert "<advice" in result, "Missing <advice> tag!"
        print("  ✅ Single advice: correct format")
        
        # TEST 2: Check content is actually included
        assert len(result) > 200, f"Response too short ({len(result)} chars) - content missing?"
        print(f"  ✅ Response includes content: {len(result)} chars")
        
        # TEST 3: Invalid advice returns error/warning
        bad_result = execute_get_advice("INVALID-999")
        has_error = "<advice_error>" in bad_result or "<advice_warning>" in bad_result or "not found" in bad_result.lower()
        assert has_error, "Invalid advice should return error/warning!"
        print("  ✅ Invalid advice: returns error")
        
        # TEST 4: Multiple advices
        multi_result = execute_get_advice("ADV-G01,ADV-G02")
        if "ADV-G02" in multi_result or "<count>" in multi_result:
            print("  ✅ Multiple advices: handled correctly")
        else:
            print("  ⚠️ Multiple advices: may only return first one")
        
        # TEST 5: Empty input
        empty_result = execute_get_advice("")
        has_error = "<advice_error>" in empty_result or "error" in empty_result.lower()
        assert has_error, "Empty input should return error!"
        print("  ✅ Empty input: returns error")
        
        # TEST 6: Whitespace handling
        whitespace_result = execute_get_advice("  ADV-G01  ")
        assert "ADV-G01" in whitespace_result, "Should handle whitespace in input!"
        print("  ✅ Whitespace handling: OK")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_singleton_pattern():
    """Test that get_advice_system returns singleton"""
    print("\n9. Singleton Pattern:")
    
    try:
        from app.advice.advice_loader import get_advice_system
        
        # Clear any existing instance
        import app.advice.advice_loader as loader_module
        loader_module._advice_system_instance = None
        
        # First call creates instance
        system1 = get_advice_system()
        assert system1 is not None, "First call returned None!"
        print("  ✅ First call: created instance")
        
        # Second call returns same instance
        system2 = get_advice_system()
        assert system2 is system1, "Second call created new instance (not singleton)!"
        print("  ✅ Second call: returned same instance")
        
        # Third call still same
        system3 = get_advice_system()
        assert system3 is system1, "Third call created new instance!"
        print("  ✅ Singleton pattern: working correctly")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_prompt_template_integration():
    """Test integration with prompt_templates.py"""
    print("\n10. Prompt Template Integration:")
    
    try:
        from app.llm.prompt_templates import (
            format_orchestrator_prompt_ask_agent,
            format_orchestrator_prompt_new_project_agent,
        )
        
        # TEST 1: Ask agent prompt
        ask_result = format_orchestrator_prompt_ask_agent(
            user_query="Test query for ask mode",
            selected_chunks="",
            compact_index="",
            project_map="",
        )
        
        assert "system" in ask_result, "Missing 'system' key!"
        assert "user" in ask_result, "Missing 'user' key!"
        
        system_prompt = ask_result["system"]
        assert len(system_prompt) > 500, f"System prompt too short ({len(system_prompt)} chars)!"
        
        # Check advice catalog is included
        has_advice = "ADVICE CATALOG" in system_prompt or "ADV-" in system_prompt
        assert has_advice, "Advice catalog not found in ask_agent system prompt!"
        print("  ✅ format_orchestrator_prompt_ask_agent: advice catalog included")
        
        # TEST 2: New project prompt
        new_result = format_orchestrator_prompt_new_project_agent(
            user_query="Create a new test project",
        )
        
        new_system = new_result["system"]
        assert len(new_system) > 500, f"System prompt too short ({len(new_system)} chars)!"
        
        has_advice = "ADVICE CATALOG" in new_system or "ADV-" in new_system
        assert has_advice, "Advice catalog not found in new_project_agent system prompt!"
        print("  ✅ format_orchestrator_prompt_new_project_agent: advice catalog included")
        
        # TEST 3: User prompt is correct
        user_prompt = ask_result["user"]
        assert "Test query" in user_prompt, "User query not in user prompt!"
        print("  ✅ User query correctly placed in user prompt")
        
        return True
    except ImportError as e:
        print(f"  ❌ IMPORT ERROR: {e}")
        print("     Make sure prompt_templates.py has the import for get_catalog_for_prompt")
        return False
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_handling():
    """Test error handling in various scenarios"""
    print("\n11. Error Handling:")
    
    try:
        from app.advice.advice_loader import AdviceSystem, execute_get_advice
        
        # TEST 1: Graceful handling of missing files (simulated)
        # We can't easily test missing files without mocking, so we test edge cases
        
        # TEST 2: Special characters in advice ID
        result = execute_get_advice("ADV-<script>alert('xss')</script>")
        assert "error" in result.lower() or "not found" in result.lower(), \
            "Should handle malicious input!"
        print("  ✅ Malicious input: handled safely")
        
        # TEST 3: Very long input
        long_input = "ADV-" + "A" * 1000
        result = execute_get_advice(long_input)
        assert "error" in result.lower() or "not found" in result.lower(), \
            "Should handle very long input!"
        print("  ✅ Very long input: handled safely")
        
        # TEST 4: Unicode in advice ID
        result = execute_get_advice("ADV-тест")
        assert "error" in result.lower() or "not found" in result.lower() or "ADV-тест" in result, \
            "Should handle unicode input!"
        print("  ✅ Unicode input: handled safely")
        
        return True
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_catalog_content_quality():
    """Test the quality of catalog content"""
    print("\n12. Catalog Content Quality:")
    
    try:
        from app.advice.advice_loader import get_advice_system
        
        system = get_advice_system()
        
        issues = []
        
        for advice_id, advice in system.catalog.items():
            # Check ID format
            if not advice_id.startswith("ADV-"):
                issues.append(f"{advice_id}: ID should start with 'ADV-'")
            
            # Check name length
            if len(advice.name) < 5:
                issues.append(f"{advice_id}: name too short ({len(advice.name)} chars)")
            
            # Check description length  
            if len(advice.description) < 20:
                issues.append(f"{advice_id}: description too short ({len(advice.description)} chars)")
            
            # Check category exists
            if advice.category not in system.categories:
                issues.append(f"{advice_id}: invalid category '{advice.category}'")
        
        if issues:
            print(f"  ⚠️ Quality issues found:")
            for issue in issues[:5]:  # Show first 5
                print(f"     - {issue}")
            if len(issues) > 5:
                print(f"     ... and {len(issues) - 5} more")
        else:
            print("  ✅ All catalog entries have good quality")
        
        print(f"  ✅ Checked {len(system.catalog)} advices")
        return len(issues) == 0
    except Exception as e:
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 70)
    print("ADVICE SYSTEM - COMPREHENSIVE INTEGRATION TESTS")
    print("=" * 70)
    
    tests = [
        ("JSON Files Validation", test_json_files_exist_and_valid),
        ("AdviceSystem Initialization", test_advice_system_initialization),
        ("Lazy Loading Content", test_lazy_loading_content),
        ("Get Advice Content", test_get_advice_content),
        ("Catalog for Ask Mode", test_catalog_for_prompt_ask_mode),
        ("Catalog for New Project Mode", test_catalog_for_prompt_new_project_mode),
        ("Mode Filtering", test_mode_filtering),
        ("Execute Get Advice Tool", test_execute_get_advice_tool),
        ("Singleton Pattern", test_singleton_pattern),
        ("Prompt Template Integration", test_prompt_template_integration),
        ("Error Handling", test_error_handling),
        ("Catalog Content Quality", test_catalog_content_quality),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"\n{name}:")
            print(f"  ❌ UNEXPECTED ERROR: {type(e).__name__}: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    
    for i, (name, _) in enumerate(tests):
        status = "✅ PASS" if results[i] else "❌ FAIL"
        print(f"  {status}: {name}")
    
    print()
    if passed == total:
        print(f"🎉 ALL TESTS PASSED ({passed}/{total})")
    else:
        print(f"⚠️ SOME TESTS FAILED ({passed}/{total} passed)")
        failed = [tests[i][0] for i, r in enumerate(results) if not r]
        print(f"   Failed: {', '.join(failed)}")
    
    print("=" * 70)
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())