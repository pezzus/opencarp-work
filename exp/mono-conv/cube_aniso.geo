SetFactory("OpenCASCADE");

DefineConstant[
  lx = {20, Name "Tissue length [mm]", Min 1, Max 40, Step 1}
  ly = {20, Name "Tissue width [mm]",  Min 1, Max 40, Step 1}
  lz = {15, Name "Tissue depth [mm]",  Min 1, Max 40, Step 1}
  ht = {0.5, Name "Transverse target size [mm]"},
  vl = {0.6, Name "Longitudinal CV"},
  vt = {0.2, Name "Transverse CV"},
  angle0 = {-60, Name "Fiber angle at zmin [deg]"},
  angle1 = {60, Name "Fiber angle at zmax [deg]"}
];

Mesh.MeshSizeFromPoints = 0;
Mesh.MeshSizeFromCurvature = 0;
Mesh.MeshSizeExtendFromBoundary = 0;

Box(1) = {0, 0, 0, lx, ly, lz};

//Physical Volume(1) = {1};

hl = ht * vl / vt;

mt = 1 / (ht * ht);
ml = 1 / (hl * hl);

theta_a = angle0;
theta_b = (angle1 - angle0) / lz;

Field[1] = MathEvalAniso;

Field[1].M11 = Sprintf("(%g) + ((%g)-(%g))*Cos(((%g) + (%g)*z)*Pi/180)^2", mt, ml, mt, theta_a, theta_b);
Field[1].M22 = Sprintf("(%g) + ((%g)-(%g))*Sin(((%g) + (%g)*z)*Pi/180)^2", mt, ml, mt, theta_a, theta_b);
Field[1].M12 = Sprintf("((%g)-(%g))*Sin(((%g) + (%g)*z)*Pi/180)*Cos(((%g) + (%g)*z)*Pi/180)",
                       ml, mt, theta_a, theta_b, theta_a, theta_b);

Field[1].M13 = "0";
Field[1].M23 = "0";
Field[1].M33 = Sprintf("%g", mt);

Background Field = 1;

//Mesh.MeshSizeMax = 0.4;

Mesh.Algorithm   = 7;
Mesh.Algorithm3D = 7;
Mesh.AnisoMax = vl / vt;
