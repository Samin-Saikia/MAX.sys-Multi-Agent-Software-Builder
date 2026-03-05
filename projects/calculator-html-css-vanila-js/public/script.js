/* -------------------------------------------------
   Calculator State Management
   ------------------------------------------------- */
let state = {
    current: '',
    previous: '',
    operator: null,
    overwrite: false   // After a result, next digit starts a new number
};

/* -------------------------------------------------
   DOM References
   ------------------------------------------------- */
const display = document.getElementById('display');
const buttons = document.querySelectorAll('button[data-action]');

/* -------------------------------------------------
   Event Registration
   ------------------------------------------------- */
buttons.forEach(btn => btn.addEventListener('click', handleButtonClick));
document.addEventListener('keydown', handleKeyboard);

/* -------------------------------------------------
   Click Handler (delegated via data-action)
   ------------------------------------------------- */
function handleButtonClick(e) {
    const { action, value } = e.target.dataset;
    switch (action) {
        case 'digit':      inputDigit(value);      break;
        case 'operator':   setOperator(value);     break;
        case 'equals':     compute();              break;
        case 'clear':      clearAll();             break;
        case 'backspace': backspace();            break;
        case 'decimal':    inputDecimal();         break;
    }
    updateDisplay();
}

/* -------------------------------------------------
   Core Calculator Functions
   ------------------------------------------------- */
function inputDigit(digit) {
    if (state.overwrite) {
        state.current = digit;
        state.overwrite = false;
    } else {
        state.current += digit;
    }
}

function inputDecimal() {
    if (!state.current.includes('.')) {
        state.current += '.';
    }
}

function setOperator(op) {
    // If there is already a pending operation, compute it first
    if (state.operator && !state.overwrite) {
        compute();
    }
    state.previous = state.current;
    state.operator = op;
    state.overwrite = true;
}

function compute() {
    const prev = parseFloat(state.previous);
    const curr = parseFloat(state.current);
    if (isNaN(prev) || isNaN(curr)) return;

    let result;
    switch (state.operator) {
        case '+': result = prev + curr; break;
        case '-': result = prev - curr; break;
        case '*': result = prev * curr; break;
        case '/':
            result = (curr === 0) ? 'Error' : prev / curr;
            break;
        default: return;
    }

    state.current = result.toString();
    state.previous = '';
    state.operator = null;
    state.overwrite = true;
}

function clearAll() {
    state.current = '';
    state.previous = '';
    state.operator = null;
    state.overwrite = false;
}

function backspace() {
    if (state.overwrite) {
        state.current = '';
        state.overwrite = false;
    } else {
        state.current = state.current.slice(0, -1);
    }
}

/* -------------------------------------------------
   UI Update
   ------------------------------------------------- */
function updateDisplay() {
    display.textContent = state.current || '0';
}

/* -------------------------------------------------
   Keyboard Support
   ------------------------------------------------- */
function handleKeyboard(e) {
    if (e.key >= '0' && e.key <= '9') {
        inputDigit(e.key);
    } else if (e.key === '.') {
        inputDecimal();
    } else if (['+', '-', '*', '/'].includes(e.key)) {
        setOperator(e.key);
    } else if (e.key === 'Enter' || e.key === '=') {
        compute();
    } else if (e.key === 'Backspace') {
        backspace();
    } else if (e.key === 'Escape') {
        clearAll();
    } else {
        return; // ignore unrelated keys
    }
    e.preventDefault(); // stop default scrolling etc.
    updateDisplay();
}

/* -------------------------------------------------
   Export for potential unit‑testing (optional)
   ------------------------------------------------- */
export const calculator = {
    inputDigit,
    inputDecimal,
    setOperator,
    compute,
    clearAll,
    backspace,
    getDisplay: () => state.current || '0'
};
