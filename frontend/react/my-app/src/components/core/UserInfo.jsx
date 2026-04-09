function UserInfo() {
  const user = {
    name: "Khaleel",
    age: 25,
    city: "Vizag",
  };

  return (
    <>
      <h1>Name: {user.name}</h1>
      <p>Age: {user.age}</p>
      <p>City: {user.city}</p>
    </>
  );
}

export default UserInfo;