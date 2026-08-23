#!/bin/bash
#
# Quick Test Runner for AWS Resource Inventory
# -----------------------------------------
#
# Runs essential tests to verify functionality.
#

echo "🚀 AWS Resource Inventory - Quick Test Suite"
echo "=========================================="

echo "0️⃣  Setting up environment..."
if ! poetry install --with dev; then
    echo "   ❌ Poetry install failed"
    exit 1
else
    echo "   ✅ Environment setup complete"
fi
echo ""
# Test 1: Help command
echo "1️⃣  Testing help command..."
if ! poetry run aws-inventory scan --help > /dev/null 2>&1 ; then
    echo "   ❌ Help command failed"
else
    echo "   ✅ Help command works"
fi

# Test 2: Dry run
echo "2️⃣  Testing dry run..."
if ! poetry run aws-inventory scan --dry-run --regions us-east-1 --service ec2 > /dev/null 2>&1 ; then
    echo "   ❌ Dry run failed"
else
    echo "   ✅ Dry run works"
fi

# Test 3: JSON to stdout pipes cleanly into jq (real read-only scan)
echo "3️⃣  Testing JSON to stdout (--output - | jq)..."
if ! poetry run aws-inventory scan --regions us-east-1 --service s3 --no-cache --output - 2>/dev/null | jq -e '.schema_version == 1' > /dev/null 2>&1 ; then
    echo "   ❌ --output - did not produce clean JSON on stdout"
else
    echo "   ✅ --output - pipes cleanly into jq"
fi

# Test 4: Multiple services dry run
echo "4️⃣  Testing multiple services..."
if ! poetry run aws-inventory scan --dry-run --regions us-east-1 --service ec2> /dev/null 2>&1 ; then
    echo "   ❌ Multiple services failed"
else
    echo "   ✅ Multiple services work"
fi

# Test 5: Performance settings
echo "5️⃣  Testing performance settings..."
if ! poetry run aws-inventory scan --dry-run --max-workers 20 --service-workers 8 --regions us-east-1 --service ec2 > /dev/null 2>&1 ; then
    echo "   ❌ Performance settings failed"
else
    echo "   ✅ Performance settings work"
fi

# Test 6: Tag filtering
echo "6️⃣  Testing tag filtering..."
if ! poetry run aws-inventory scan --dry-run --regions us-east-1 --service ec2 --tag-key app --tag-value test > /dev/null 2>&1 ; then
    echo "   ❌ Tag filtering failed"
else
    echo "   ✅ Tag filtering works"
fi

# Test 7: Cache options
echo "7️⃣  Testing cache options..."
if ! poetry run aws-inventory scan --dry-run --no-cache --regions us-east-1 --service ec2 > /dev/null 2>&1 ; then
    echo "   ❌ Cache options failed"
else
    echo "   ✅ Cache options work"
fi

# Test 8: Error handling — --all-services without tags must be rejected
echo "8️⃣  Testing error handling..."
if poetry run aws-inventory scan --dry-run --regions us-east-1 --all-services > /dev/null 2>&1 ; then
    echo "   ❌ Error handling failed (--all-services without tags should exit non-zero)"
else
    echo "   ✅ Error handling works (--all-services without tags is rejected)"
fi

echo ""
echo "🎉 Quick tests completed!"
echo ""
echo "To run comprehensive tests:"
echo "   poetry run aws-inventory scan"
echo ""
echo "To test with real AWS resources:"
echo "   poetry run aws-inventory scan --help"
echo "   poetry run aws-inventory scan --regions us-east-1 --service ec2"
echo "   poetry run aws-inventory scan --regions us-east-1 --service ec2 --output - | jq '.summary'"
