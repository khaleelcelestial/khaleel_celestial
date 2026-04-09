import { useState } from "react";
function Login() {
    const [login,setLogin] = useState(false);
    return (
        <button onClick = {() => setLogin(!login)}>
            {login ? "LogOut" : "Login"}
        </button>
    );

}

export default Login;