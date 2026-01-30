from app.asl.tools import calculate_drm, execute_tool
import json

def test_calculate_drm_robustness():
    print("Running calculate_drm robustness tests...")
    
    # Test case 1: is_moving_in_open=False, is_moving=False, other=[1]
    result1 = calculate_drm(is_moving_in_open=False, is_moving=False, other=[1])
    print(f"Test 1 (is_moving_in_open=False, other=[1]): {result1['total_drm']} (Expected: 1)")
    assert result1['total_drm'] == 1
    assert result1['components']['ffmo'] == 0
    assert result1['components']['ffnam'] == 0
    
    # Test case 2: Moving (not in open)
    result2 = calculate_drm(is_moving=True, other=[0])
    # Expected: 0 (FFMO) + -1 (FFNAM) = -1
    print(f"Test 2 (is_moving=True): {result2['total_drm']} (Expected: -1)")
    assert result2['total_drm'] == -1
    assert result2['components']['ffmo'] == 0
    assert result2['components']['ffnam'] == -1

    # Test case 3: Moving in open (should apply both FFMO and FFNAM)
    result3 = calculate_drm(is_moving_in_open=True, other=[1])
    # Expected: -1 (FFMO) + -1 (FFNAM) + 1 (Other) = -1
    print(f"Test 3 (is_moving_in_open=True, other=[1]): {result3['total_drm']} (Expected: -1)")
    assert result3['total_drm'] == -1
    assert result3['components']['ffmo'] == -1
    assert result3['components']['ffnam'] == -1
    
    # Test case 4: Assault movement (should apply FFMO only if in open)
    result4 = calculate_drm(is_moving_in_open=True, is_assault_movement=True, hindrance=1)
    # Expected: -1 (FFMO) + 0 (Assault Movement) + 1 (Hindrance) = 0
    print(f"Test 4 (is_moving_in_open=True, is_assault_movement=True, hindrance=1): {result4['total_drm']} (Expected: 0)")
    assert result4['total_drm'] == 0
    assert result4['components']['ffmo'] == -1
    assert result4['components']['ffnam'] == 0
    
    # Test case 5: via execute_tool (dispatch logic)
    args = {
        "is_moving_in_open": True,
        "is_assault_movement": False,
        "other": [1, 1],
        "terrain_tem": 2
    }
    result5 = execute_tool("calculate_drm", args)
    # Expected: -1 (FFMO) + -1 (FFNAM) + 2 (Other) + 2 (Terrain) = 2
    print(f"Test 5 (via execute_tool): {result5['total_drm']} (Expected: 2)")
    assert result5['total_drm'] == 2

    print("All tests passed! ✅")

    # Test case 5: Explicitly verify old parameters cause error
    print("Test 5: Verifying 'ffmo' parameter is rejected...")
    try:
        execute_tool("calculate_drm", {"ffmo": -1})
        assert False, "Should have raised TypeError"
    except TypeError as e:
        print(f"✅ Correctly rejected 'ffmo': {e}")

    print("All tests passed! ✅")

if __name__ == "__main__":
    try:
        test_calculate_drm_robustness()
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
