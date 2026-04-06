class Shape {
  constructor(name) {
    this.name = name;
  }
  area() {
    throw Error("Not implemented");
  }
  perimeter() {
    throw Error("Not implemented");
  }
}

class Circle extends Shape {
  constructor(r) {
    super("Circle");
    this.r = r;
  }
  area() {
    return Math.PI * this.r ** 2;
  }
  perimeter() {
    return 2 * Math.PI * this.r;
  }
}

class Rectangle extends Shape {
  constructor(w, h) {
    super("Rectangle");
    this.w = w;
    this.h = h;
  }
  area() {
    return this.w * this.h;
  }
  perimeter() {
    return 2 * (this.w + this.h);
  }
}

class Triangle extends Shape {
  constructor(a, b, c) {
    super("Triangle");
    this.a = a;
    this.b = b;
    this.c = c;
  }
  perimeter() {
    return this.a + this.b + this.c;
  }
  area() {
    const s = this.perimeter() / 2;
    return Math.sqrt(s * (s - this.a) * (s - this.b) * (s - this.c));
  }
}

function printShapeReport(shapes) {
  let total = 0;
  console.log("Shape Report:");
  console.log("--------------------------");

  shapes.forEach(s => {
    const area = s.area();
    const peri = s.perimeter();
    total += area;

    console.log(
      `${s.name.padEnd(12)} | Area: ${area.toFixed(2)} | Perimeter: ${peri.toFixed(2)}`
    );
  });

  console.log("--------------------------");
  console.log("Total Area:", total.toFixed(2));
}

// Test
const shapes = [
  new Circle(10),
  new Rectangle(5, 8),
  new Triangle(3, 4, 5),
];

printShapeReport(shapes);