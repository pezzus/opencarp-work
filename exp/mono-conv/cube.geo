SetFactory("OpenCASCADE");

DefineConstant[
  lx = {20, Name "Tissue length [mm]", Min 1, Max 40, Step 1}
  ly = {20, Name "Tissue width [mm]",  Min 1, Max 40, Step 1}
  lz = {15, Name "Tissue depth [mm]",  Min 1, Max 40, Step 1}
  minlc = {0.5, Name "Min mesh length [mm]", Min 0.001, Max 100.0, Step 0.01}
  maxlc = {0.5, Name "Max mesh length [mm]", Min 0.001, Max 100.0, Step 0.01}
];

Mesh.CharacteristicLengthMin = minlc;
Mesh.CharacteristicLengthMax = maxlc;

Mesh.Algorithm3D = 10; // HXT
General.NumThreads = 8;

Mesh.MeshSizeFromPoints = 0;
Mesh.MeshSizeFromCurvature = 0;
Mesh.MeshSizeExtendFromBoundary = 0;

Box(1) = {0, 0, 0, lx, ly, lz};

Physical Volume(1) = {1};
