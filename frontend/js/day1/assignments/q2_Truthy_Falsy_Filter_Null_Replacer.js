function cleanData(arr) {
    return arr.filter(Boolean);
}

function replaceNulls(arr, replacement) {
    return arr.map(item => item == null ? replacement : item);
}

// Test
const data = [0, "hello", null, 42, "", undefined, false, "world", NaN];

console.log(cleanData(data)); 
// ["hello", 42, "world"]

console.log(replaceNulls(data, "N/A")); 
// [0, "hello", "N/A", 42, "", "N/A", false, "world", NaN]